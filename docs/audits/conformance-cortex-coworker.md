# Conformance audit: ICDEV Cortex and Coworker (icx-cnf-01)

CUI // SP-CTI

ICOS card `osx-fth-30`, ICDEV board card `icx-cnf-01` (Phase 287 of the follow-through programme).

## Why

On 2026-11-14 the owner set five rules for everything built. Every feature has:

1. a console GUI;
2. a declared workflow for every decision a person takes;
3. enterprise-twin observation, with each finding's thread and a replay;
4. a place in the architecture model, with requirements traced to tests;
5. an AI-native capability that only reads or proposes.

ICDEV[IT] had no per-feature check for any of them. This audit is that check. It starts with
ICDEV Cortex (`tools/cortex`), the ACE Co-Worker Engine (`tools/ace`) and the Co-Workers canvas
(`tools/coworkers`). Any feature in `args/component_registry.yaml` joins by being listed in
`args/feature_conformance.yaml`.

## Method

`python tools/conformance/feature_audit.py` judges each listed feature. The config says only
which features are in scope and which surfaces (Flask endpoints, MCP tools) each one owns. Every
verdict comes from the repository, and every cell lists the evidence it rests on.

| Rule | Passes when | Evidence read |
|---|---|---|
| GUI | A GET route of the feature renders a template; one such page is in the nav or one link from a nav page; every mutating route is called by a page. A page names a route only by its Flask endpoint id (icx-cnf-08), and every write a page names was sent by a page in CI's e2e run. | The live `url_map`, read in a scrubbed subprocess with the probe environment of `tools/dashboard/nav_paths.py` (every registry toggle on); view source; registry and `base.html` nav hrefs; the endpoint ids each template and first-party script names in code (`icdevRoute('<endpoint>')`, `icdevFetch('<endpoint>')`, `url_for('<endpoint>')`), comments blanked; the e2e record `tests/e2e/observed-calls.json`. |
| Workflow | Each decision maps to a template under `args/workflow_hitl_templates/` (its `decides:` names the surface) with a manual stage held by a role. An MCP decision may instead sit in an approval tier that halts. A stop stays immediate, with a stated reason. | Decision verbs in the endpoint or tool name (launch, rerun, abort, confirm, decline, approve, reject, delete, award, adjudicate, sign, waive, release, hitl); `HITLGate`, `approval_gate`, `requires_confirm`, `confirm_proposal` in the view source; decisions the config declares with a quotation that must exist in the named file; `args/agent_approval_policy.yaml`. |
| Twin | A `@register_twin` adapter in `tools/twin_core/adapters/` has the component as `canvas_key` (or names the feature in `observes`), carries `thread_id` on its violations and defines `replay()`. | The adapter source, parsed without importing it. |
| Architecture | Each requirement for the feature in `args/platform_model/*.yaml` names elements of a digital-thread artifact type (`tools/mbse/digital_thread.py` `VALID_TYPES`), each linked to existing code, and traces to an existing test; no element of the feature is an orphan. | The platform model files (the reviewed source the MBSE digital thread is seeded from), the files they name. |
| AI-native | At least one MCP tool of the feature is declared read-only with `tool_authorization` tier `read`; every mutating tool sits in an approval tier that halts. | `tools/mcp/tool_registry.py` (`list_tools`, `READ_ONLY_DECLARATIONS`, `tool_authorization`), `args/agent_approval_policy.yaml`. |

Rules that do not apply take a stated reason under the feature's `reasons:`. A reason excuses only
its own rule. A reason the evidence no longer needs is stale, and `--check` fails on it. The same
goes for `stops:`, `not_decisions:` and `machine_endpoints:` (a REST surface for external callers
that no page needs to call). `served_by:` lets a page another feature owns count as this feature's
console page; it must quote text that proves it.

Nothing is skipped silently. Every endpoint of the Cortex, ACE and Co-Workers blueprints, and every
MCP tool named `cortex_*` or `ace_*`, must be owned by exactly one feature, or `--check` fails.

The output has no timestamps and no line numbers, so two runs over one tree are byte-identical.
`--markdown` rewrites the tables below and the JSON snapshot
(`docs/audits/conformance-cortex-coworker.json`). `--census` rewrites
`args/feature_conformance_census.txt`, which lists every open gap by name. `census_max` may only go
down. `--check` exits 1 on a stale doc, snapshot, reason or census.

The coherence check `feature_conformance` fails on any open gap or stale record: `enforcement: fail`
since icx-cnf-07, with `census_max: 0`. It is in the full tier because the url_map probe takes about
25 seconds; the fast tier runs it when a diff touches an audited surface.

The audit follows its own rules: the console page `/twin-observatory/conformance` (linked from the
Twin Observatory), the JSON route `/twin-observatory/api/conformance`, and the read-only MCP tool
`feature_conformance`. It decides nothing, so it has no workflow. It is row `conformance.audit`
below, with its own twin and architecture gaps.

## Results

<!-- BEGIN GENERATED feature-conformance -->
_Generated by `python tools/conformance/feature_audit.py --markdown --census`. Do not edit between the markers._

31 features; 31 meet all five rules; 0 open gaps. Enforcement: `fail`.

### Matrix

| Feature | GUI | Workflow | Twin | Architecture | AI-native |
|---|---|---|---|---|---|
| ACE: abort (`ace.abort`) | pass | pass | pass | pass | excused |
| ACE: mid-run HITL checkpoint, suggested-team confirm (`ace.hitl`) | pass | pass | pass | pass | pass |
| ACE: role profiles (`ace.profiles`) | pass | pass | pass | pass | pass |
| ACE: launch, run, rerun (`ace.runs`) | pass | pass | pass | pass | pass |
| ACE: trust (NOVA), sessions, evaluations, trends, event feed and auto-dispatch (`ace.trust_sessions`) | pass | pass | pass | pass | pass |
| Engineering CoE: architecture decision records as signed, versioned records (`coe.adrs`) | pass | pass | pass | pass | pass |
| Engineering CoE: scorecards against standards, delivery evidence, conformance bundles (`coe.conformance`) | pass | pass | pass | pass | pass |
| Engineering CoE: scorecard exemptions that expire (`coe.exemptions`) | pass | pass | pass | pass | pass |
| Engineering CoE: golden paths, a new service conformant by construction (`coe.golden_paths`) | pass | pass | pass | pass | pass |
| Engineering CoE: standards register with a lifecycle (`coe.standards`) | pass | pass | pass | pass | pass |
| Feature conformance audit (this tool) (`conformance.audit`) | pass | n/a | pass | pass | pass |
| Cortex: agent launch (`cortex.agent_launch`) | pass | pass | pass | pass | pass |
| Cortex: air-gap guard and health (`cortex.airgap_guard`) | pass | n/a | pass | pass | pass |
| Cortex: resolution findings (`cortex.findings`) | pass | pass | pass | pass | pass |
| Cortex: govern (TRUST chain) (`cortex.govern`) | pass | n/a | pass | pass | pass |
| Cortex: resolve, complete, reason, classify, extract (`cortex.governed_verbs`) | pass | n/a | pass | pass | pass |
| Cortex: intake bridge (`cortex.intake`) | pass | n/a | pass | pass | pass |
| Cortex: metrics (`cortex.metrics`) | pass | n/a | pass | pass | pass |
| Cortex: PMO verbs (slides, win themes, staffing, cost, dashboard, award, BOM) (`cortex.pmo`) | pass | pass | pass | pass | pass |
| Cortex: search and ask (`cortex.search_ask`) | pass | pass | pass | pass | pass |
| Cortex: service keys (`cortex.service_keys`) | pass | n/a | pass | pass | pass |
| Co-Workers canvas (`cwk.canvas`) | pass | n/a | pass | pass | pass |
| Evidence collectors: read-only customer sources, objective evidence for ICDEV[OS] (`evidence_collectors.objective_evidence`) | pass | pass | pass | pass | pass |
| Signed factory builds: signed artefacts with SBOM/AI-BOM, provenance and VEX for ICDEV[OS] (`factory_builds.signed_builds`) | pass | pass | pass | pass | pass |
| Modernisation: AI-Ify opportunities, PRDs and their promotion (`mdz.aiify`) | pass | pass | pass | pass | pass |
| Modernisation: cloud-native targets (container, config, twelve factors, events) (`mdz.cloud_native`) | pass | n/a | pass | pass | pass |
| Modernisation: the ICDEV[OS] apptwin exporter and decision import (`mdz.exporter`) | pass | pass | pass | pass | pass |
| Modernisation: Process-Ify (document or observed process to workflow) (`mdz.processify`) | pass | pass | pass | pass | pass |
| Modernisation: 7R assessment and the strategy ICDEV proposes (`mdz.seven_r`) | pass | pass | pass | pass | pass |
| Modernisation: an application's waves and CAM refactor jobs (`mdz.waves_cam`) | pass | pass | pass | pass | pass |
| Store publisher: signed agent-store packages for ICDEV[OS], prepared by one person and published by another (`store_publisher.signed_packages`) | pass | pass | pass | pass | pass |

### Gaps per rule

| Rule | Open gaps |
|---|---|
| GUI | 0 |
| Workflow | 0 |
| Twin | 0 |
| Architecture | 0 |
| AI-native | 0 |

### Routes: named by id, seen in e2e

Of 196 routes, 164 are named by a page by endpoint id (`icdevRoute`/`icdevFetch` in tools/dashboard/static/js/routes.gen.js, or `url_for`), and a page was seen sending 144 in the recorded e2e run (`tests/e2e/observed-calls.json`). Of the 79 writes a page names, 79 were seen: CI's e2e job fails on any it does not see (`--e2e-gate`). Reads may be credited by id alone.

| Feature | Route | Endpoint | Named by id in | Seen in e2e |
|---|---|---|---|---|
| `ace.abort` | `POST /api/ace/<instance_id>/abort` | `ace_api.api_abort` | tools/dashboard/templates/coworker/_dispatch_panels.html, tools/dashboard/templates/coworker/instance.html | ✓ |
| `ace.hitl` | `POST /api/ace/<instance_id>/hitl` | `ace_api.api_hitl_resolve` | tools/dashboard/templates/coworker/hitl.html | ✓ |
| `ace.hitl` | `GET /api/ace/<instance_id>/hitl/pending` | `ace_api.api_hitl_pending` | — |  |
| `ace.hitl` | `GET /api/ace/suggestions/<suggestion_id>` | `ace_api.api_suggestion_get` | — |  |
| `ace.hitl` | `POST /api/ace/suggestions/<suggestion_id>/confirm` | `ace_api.api_suggestion_confirm` | tools/dashboard/templates/includes/_chat_action_card.html | ✓ |
| `ace.hitl` | `POST /api/ace/suggestions/<suggestion_id>/decline` | `ace_api.api_suggestion_decline` | tools/dashboard/templates/includes/_chat_action_card.html | ✓ |
| `ace.profiles` | `GET /api/ace/presets` | `ace_api.api_presets_list` | — |  |
| `ace.profiles` | `GET /api/ace/profiles` | `ace_api.api_profiles_list` | — |  |
| `ace.profiles` | `DELETE /api/ace/profiles/<role_id>` | `ace_api.api_profiles_delete` | tools/dashboard/templates/coworker/roles.html | ✓ |
| `ace.profiles` | `POST /api/ace/profiles/generate` | `ace_api.api_profiles_generate` | tools/dashboard/templates/coworker/profile_new.html | ✓ |
| `ace.profiles` | `POST /api/ace/profiles/preview` | `ace_api.api_profiles_preview` | tools/dashboard/templates/coworker/profile_new.html | ✓ |
| `ace.profiles` | `POST /api/ace/profiles/suggest-name` | `ace_api.api_profiles_suggest_name` | tools/dashboard/templates/coworker/profile_new.html | ✓ |
| `ace.profiles` | `GET /coworker/profiles/new` | `ace.profiles_new` | tools/dashboard/templates/coworker/profile_new.html, tools/dashboard/templates/coworker/roles.html | ✓ |
| `ace.profiles` | `GET /coworker/roles` | `ace.roles` | tools/dashboard/templates/coworker/index.html, tools/dashboard/templates/coworker/profile_new.html | ✓ |
| `ace.runs` | `GET /api/ace/<instance_id>/artifacts` | `ace_api.api_artifacts` | — |  |
| `ace.runs` | `GET /api/ace/<instance_id>/audit` | `ace_api.api_audit` | tools/dashboard/templates/coworker/instance.html | ✓ |
| `ace.runs` | `GET /api/ace/<instance_id>/messages` | `ace_api.api_messages` | tools/dashboard/templates/coworker/instance.html | ✓ |
| `ace.runs` | `GET /api/ace/<instance_id>/status` | `ace_api.api_status` | tools/dashboard/templates/coworker/instance.html | ✓ |
| `ace.runs` | `GET /api/ace/<instance_id>/stream` | `ace_api.api_stream` | tools/dashboard/templates/ace/live.html |  |
| `ace.runs` | `POST /api/ace/chat` | `ace_api.api_ace_chat` | tools/dashboard/templates/ace/chat.html | ✓ |
| `ace.runs` | `GET /api/ace/instances` | `ace_api.api_instances` | — |  |
| `ace.runs` | `POST /api/ace/launch` | `ace_api.api_launch` | tools/dashboard/templates/coworker/index.html, tools/dashboard/templates/workflow_canvas/processify.html | ✓ |
| `ace.runs` | `POST /api/ace/sessions/<session_id>/rerun` | `ace_api.api_session_rerun` | tools/dashboard/templates/ace/session_detail.html | ✓ |
| `ace.runs` | `GET /coworker` | `ace.index` | tools/dashboard/templates/ace/chat.html, tools/dashboard/templates/ace/evals.html, tools/dashboard/templates/ace/live.html, tools/dashboard/templates/ace/session_detail.html, tools/dashboard/templates/ace/sessions.html, tools/dashboard/templates/ace/trends.html, tools/dashboard/templates/ace/trust.html, tools/dashboard/templates/coworker/instance.html, tools/dashboard/templates/coworker/profile_new.html, tools/dashboard/templates/coworker/roles.html, tools/dashboard/templates/coworker/trust.html | ✓ |
| `ace.runs` | `GET /coworker/` | `ace.index` | tools/dashboard/templates/ace/chat.html, tools/dashboard/templates/ace/evals.html, tools/dashboard/templates/ace/live.html, tools/dashboard/templates/ace/session_detail.html, tools/dashboard/templates/ace/sessions.html, tools/dashboard/templates/ace/trends.html, tools/dashboard/templates/ace/trust.html, tools/dashboard/templates/coworker/instance.html, tools/dashboard/templates/coworker/profile_new.html, tools/dashboard/templates/coworker/roles.html, tools/dashboard/templates/coworker/trust.html | ✓ |
| `ace.runs` | `GET /coworker/<instance_id>` | `ace.instance_detail` | tools/dashboard/templates/ace/live.html, tools/dashboard/templates/coworker/_dispatch_panels.html, tools/dashboard/templates/coworker/index.html, tools/dashboard/templates/coworker/trust.html | ✓ |
| `ace.runs` | `GET /coworker/<instance_id>/resume` | `ace.instance_resume` | tools/dashboard/templates/coworker/instance.html |  |
| `ace.runs` | `GET /coworker/chat` | `ace.chat_page` | tools/dashboard/templates/ace/sessions.html | ✓ |
| `ace.runs` | `GET /coworker/live/<instance_id>` | `ace.live_monitor` | tools/dashboard/templates/ace/session_detail.html |  |
| `ace.trust_sessions` | `GET /api/ace/<instance_id>/nova-state` | `ace_api.api_nova_state` | tools/dashboard/templates/coworker/instance.html | ✓ |
| `ace.trust_sessions` | `GET /api/ace/auto-dispatch` | `ace_api.api_auto_dispatch` | tools/dashboard/templates/coworker/_dispatch_panels.html | ✓ |
| `ace.trust_sessions` | `POST /api/ace/auto-dispatch/<grant_id>/revoke` | `ace_api.api_auto_dispatch_revoke` | tools/dashboard/templates/coworker/_dispatch_panels.html | ✓ |
| `ace.trust_sessions` | `POST /api/ace/auto-dispatch/grant` | `ace_api.api_auto_dispatch_grant` | tools/dashboard/templates/coworker/_dispatch_panels.html | ✓ |
| `ace.trust_sessions` | `POST /api/ace/auto-dispatch/resume-all` | `ace_api.api_auto_dispatch_resume_all` | tools/dashboard/templates/coworker/_dispatch_panels.html | ✓ |
| `ace.trust_sessions` | `POST /api/ace/auto-dispatch/stop-all` | `ace_api.api_auto_dispatch_stop_all` | tools/dashboard/templates/coworker/_dispatch_panels.html | ✓ |
| `ace.trust_sessions` | `GET /api/ace/coworkers/<coworker_id>/stats` | `ace_api.api_coworker_stats` | — |  |
| `ace.trust_sessions` | `GET /api/ace/evals` | `ace_api.api_evals_list` | tools/dashboard/templates/ace/evals.html |  |
| `ace.trust_sessions` | `GET /api/ace/evals/trends` | `ace_api.api_evals_trends` | tools/dashboard/templates/ace/trends.html |  |
| `ace.trust_sessions` | `GET /api/ace/events` | `ace_api.api_ace_events` | tools/dashboard/templates/coworker/_dispatch_panels.html | ✓ |
| `ace.trust_sessions` | `GET /api/ace/events/topics` | `ace_api.api_ace_event_topics` | — |  |
| `ace.trust_sessions` | `POST /api/ace/iqe-query` | `ace_api.api_iqe_query` | tools/dashboard/templates/ace/chat.html, tools/dashboard/templates/coworker/index.html | ✓ |
| `ace.trust_sessions` | `GET /api/ace/monitor-summary` | `ace_api.api_monitor_summary` | — |  |
| `ace.trust_sessions` | `GET /api/ace/sessions` | `ace_api.api_sessions_list` | tools/dashboard/templates/ace/sessions.html |  |
| `ace.trust_sessions` | `GET /api/ace/sessions/<session_id>` | `ace_api.api_session_detail` | tools/dashboard/templates/ace/session_detail.html | ✓ |
| `ace.trust_sessions` | `GET /api/ace/sessions/<session_id>/eval` | `ace_api.api_session_eval_get` | tools/dashboard/templates/ace/session_detail.html | ✓ |
| `ace.trust_sessions` | `POST /api/ace/sessions/<session_id>/eval` | `ace_api.api_session_eval_post` | tools/dashboard/templates/ace/session_detail.html | ✓ |
| `ace.trust_sessions` | `GET /api/ace/sessions/<session_id>/eval/compare` | `ace_api.api_session_eval_compare` | tools/dashboard/templates/ace/session_detail.html |  |
| `ace.trust_sessions` | `GET /api/ace/sessions/<session_id>/eval/suggestions` | `ace_api.api_session_eval_suggestions` | tools/dashboard/templates/ace/session_detail.html | ✓ |
| `ace.trust_sessions` | `GET /coworker/evals` | `ace.evals_page` | tools/dashboard/templates/ace/trends.html |  |
| `ace.trust_sessions` | `GET /coworker/evals/trends` | `ace.evals_trends_page` | tools/dashboard/templates/ace/evals.html |  |
| `ace.trust_sessions` | `GET /coworker/sessions` | `ace.sessions_list` | tools/dashboard/templates/ace/chat.html, tools/dashboard/templates/ace/live.html, tools/dashboard/templates/ace/session_detail.html, tools/dashboard/templates/coworker/index.html |  |
| `ace.trust_sessions` | `GET /coworker/sessions/<session_id>` | `ace.session_detail` | tools/dashboard/templates/ace/evals.html, tools/dashboard/templates/ace/sessions.html | ✓ |
| `ace.trust_sessions` | `GET /coworker/trust` | `ace.trust_leaderboard` | tools/dashboard/templates/coworker/index.html | ✓ |
| `coe.adrs` | `GET /idp/adrs` | `idp.idp_adrs_page` | tools/dashboard/templates/idp/conformance.html, tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.adrs` | `GET /idp/api/adrs` | `idp.idp_api_adrs` | tools/dashboard/templates/idp/adrs.html | ✓ |
| `coe.adrs` | `GET /idp/api/adrs/<adr_id>` | `idp.idp_api_adr` | tools/dashboard/templates/idp/adrs.html | ✓ |
| `coe.adrs` | `POST /idp/api/adrs/<adr_id>/accept` | `idp.idp_api_adr_accept` | tools/dashboard/templates/idp/adrs.html | ✓ |
| `coe.adrs` | `POST /idp/api/adrs/<adr_id>/supersede` | `idp.idp_api_adr_supersede` | tools/dashboard/templates/idp/adrs.html | ✓ |
| `coe.adrs` | `POST /idp/api/adrs/propose` | `idp.idp_api_adr_propose` | tools/dashboard/templates/idp/adrs.html | ✓ |
| `coe.conformance` | `GET /idp/api/conformance` | `idp.idp_api_conformance` | — |  |
| `coe.conformance` | `GET /idp/api/conformance/<repo>` | `idp.idp_api_conformance_repo` | tools/dashboard/templates/idp/conformance.html | ✓ |
| `coe.conformance` | `GET /idp/api/conformance/<repo>/bundle` | `idp.idp_api_conformance_bundle` | tools/dashboard/templates/idp/conformance.html |  |
| `coe.conformance` | `POST /idp/api/conformance/<repo>/prepare` | `idp.idp_api_conformance_prepare` | tools/dashboard/templates/idp/conformance.html | ✓ |
| `coe.conformance` | `POST /idp/api/conformance/<repo>/publish` | `idp.idp_api_conformance_publish` | tools/dashboard/templates/idp/conformance.html | ✓ |
| `coe.conformance` | `GET /idp/conformance` | `idp.idp_conformance_page` | tools/dashboard/templates/idp/adrs.html, tools/dashboard/templates/idp/exemptions.html, tools/dashboard/templates/idp/golden_paths.html, tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.exemptions` | `GET /idp/api/exemptions` | `idp.idp_api_exemptions` | tools/dashboard/templates/idp/exemptions.html | ✓ |
| `coe.exemptions` | `GET /idp/api/exemptions/<ref>` | `idp.idp_api_exemption` | tools/dashboard/templates/idp/exemptions.html | ✓ |
| `coe.exemptions` | `POST /idp/api/exemptions/<ref>/decide` | `idp.idp_api_exemption_decide` | tools/dashboard/templates/idp/exemptions.html | ✓ |
| `coe.exemptions` | `POST /idp/api/exemptions/<ref>/renew` | `idp.idp_api_exemption_renew` | tools/dashboard/templates/idp/exemptions.html | ✓ |
| `coe.exemptions` | `POST /idp/api/exemptions/<ref>/renewal` | `idp.idp_api_exemption_renewal` | tools/dashboard/templates/idp/exemptions.html | ✓ |
| `coe.exemptions` | `POST /idp/api/exemptions/<ref>/revoke` | `idp.idp_api_exemption_revoke` | tools/dashboard/templates/idp/exemptions.html | ✓ |
| `coe.exemptions` | `POST /idp/api/exemptions/request` | `idp.idp_api_exemption_request` | tools/dashboard/templates/idp/exemptions.html | ✓ |
| `coe.exemptions` | `GET /idp/exemptions` | `idp.idp_exemptions_page` | tools/dashboard/templates/idp/conformance.html, tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.golden_paths` | `GET /idp/api/golden-paths` | `idp.idp_api_golden_paths` | — |  |
| `coe.golden_paths` | `GET /idp/api/golden-paths/<path_id>` | `idp.idp_api_golden_path` | tools/dashboard/templates/idp/golden_paths.html | ✓ |
| `coe.golden_paths` | `POST /idp/api/golden-paths/<path_id>/drift` | `idp.idp_api_golden_path_drift` | tools/dashboard/templates/idp/golden_paths.html | ✓ |
| `coe.golden_paths` | `POST /idp/api/golden-paths/<path_id>/publish` | `idp.idp_api_golden_path_publish` | tools/dashboard/templates/idp/golden_paths.html | ✓ |
| `coe.golden_paths` | `POST /idp/api/golden-paths/<path_id>/retire` | `idp.idp_api_golden_path_retire` | tools/dashboard/templates/idp/golden_paths.html | ✓ |
| `coe.golden_paths` | `POST /idp/api/golden-paths/propose` | `idp.idp_api_golden_path_propose` | tools/dashboard/templates/idp/golden_paths.html | ✓ |
| `coe.golden_paths` | `GET /idp/golden-paths` | `idp.idp_golden_paths_page` | tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.standards` | `GET /idp/api/standards` | `idp.idp_api_standards` | tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.standards` | `GET /idp/api/standards-twin` | `idp.idp_api_standards_twin` | tools/dashboard/templates/idp/adrs.html, tools/dashboard/templates/idp/conformance.html, tools/dashboard/templates/idp/exemptions.html, tools/dashboard/templates/idp/golden_paths.html, tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.standards` | `GET /idp/api/standards/<standard_id>` | `idp.idp_api_standard` | tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.standards` | `POST /idp/api/standards/<standard_id>/adopt` | `idp.idp_api_standard_adopt` | tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.standards` | `POST /idp/api/standards/<standard_id>/deprecate` | `idp.idp_api_standard_deprecate` | tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.standards` | `POST /idp/api/standards/<standard_id>/review` | `idp.idp_api_standard_review` | tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.standards` | `POST /idp/api/standards/propose` | `idp.idp_api_standard_propose` | tools/dashboard/templates/idp/standards.html | ✓ |
| `coe.standards` | `GET /idp/standards` | `idp.idp_standards_page` | tools/dashboard/templates/idp/adrs.html, tools/dashboard/templates/idp/conformance.html, tools/dashboard/templates/idp/exemptions.html, tools/dashboard/templates/idp/golden_paths.html | ✓ |
| `conformance.audit` | `GET /twin-observatory/api/conformance` | `twin_observatory.api_conformance` | tools/dashboard/templates/twin_observatory/conformance.html |  |
| `conformance.audit` | `GET /twin-observatory/conformance` | `twin_observatory.conformance_page` | tools/dashboard/templates/twin_observatory/index.html | ✓ |
| `cortex.agent_launch` | `GET /cortex/api/agent-launches` | `cortex.api_agent_launches` | tools/dashboard/templates/cortex/govern.html | ✓ |
| `cortex.agent_launch` | `POST /cortex/api/v1/agent` | `cortex.api_v1_agent` | — |  |
| `cortex.airgap_guard` | `GET /cortex/airgap` | `cortex.airgap_page` | tools/dashboard/templates/cortex/index.html |  |
| `cortex.airgap_guard` | `GET /cortex/api/airgap` | `cortex.api_airgap` | — |  |
| `cortex.airgap_guard` | `GET /cortex/api/v1/health` | `cortex.api_v1_health` | tools/dashboard/templates/cortex/airgap.html |  |
| `cortex.findings` | `GET /document-intelligence/api/explorer/cortex-findings` | `dic.api_explorer_cortex_findings` | tools/dashboard/templates/document_intelligence/explorer.html |  |
| `cortex.findings` | `POST /document-intelligence/api/explorer/cortex-findings/<finding_id>/adjudicate` | `dic.api_explorer_cortex_finding_adjudicate` | tools/dashboard/templates/document_intelligence/explorer.html | ✓ |
| `cortex.findings` | `GET /document-intelligence/explorer` | `dic.explorer` | — | ✓ |
| `cortex.govern` | `GET /cortex/api/trust-chain` | `cortex.api_trust_chain` | — |  |
| `cortex.govern` | `POST /cortex/api/v1/govern` | `cortex.api_v1_govern` | tools/dashboard/templates/cortex/govern.html | ✓ |
| `cortex.govern` | `GET /cortex/govern` | `cortex.govern_page` | tools/dashboard/templates/cortex/index.html | ✓ |
| `cortex.governed_verbs` | `POST /cortex/api/v1/classify` | `cortex.api_v1_classify` | tools/dashboard/templates/cortex/verbs.html | ✓ |
| `cortex.governed_verbs` | `POST /cortex/api/v1/complete` | `cortex.api_v1_complete` | tools/dashboard/templates/cortex/verbs.html | ✓ |
| `cortex.governed_verbs` | `POST /cortex/api/v1/extract` | `cortex.api_v1_extract` | tools/dashboard/templates/cortex/verbs.html | ✓ |
| `cortex.governed_verbs` | `POST /cortex/api/v1/reason` | `cortex.api_v1_reason` | tools/dashboard/templates/cortex/verbs.html | ✓ |
| `cortex.governed_verbs` | `POST /cortex/api/v1/resolve` | `cortex.api_v1_resolve` | tools/dashboard/templates/cortex/verbs.html | ✓ |
| `cortex.governed_verbs` | `GET /cortex/verbs` | `cortex.verbs_page` | tools/dashboard/templates/cortex/index.html, tools/dashboard/templates/document_intelligence/explorer.html | ✓ |
| `cortex.intake` | `GET /cortex/api/intake/sessions` | `cortex.api_intake_sessions` | tools/dashboard/templates/cortex/intake.html | ✓ |
| `cortex.intake` | `POST /cortex/api/v1/intake/session` | `cortex.api_v1_intake_session_create` | tools/dashboard/templates/cortex/intake.html | ✓ |
| `cortex.intake` | `GET /cortex/api/v1/intake/session/<session_id>` | `cortex.api_v1_intake_session_get` | tools/dashboard/templates/cortex/intake.html |  |
| `cortex.intake` | `POST /cortex/api/v1/intake/turn` | `cortex.api_v1_intake_turn` | tools/dashboard/templates/cortex/intake.html | ✓ |
| `cortex.intake` | `GET /cortex/intake` | `cortex.intake_page` | tools/dashboard/templates/cortex/index.html | ✓ |
| `cortex.metrics` | `GET /cortex/api/metrics` | `cortex.api_metrics` | — |  |
| `cortex.metrics` | `GET /cortex/api/metrics/tile` | `cortex.api_metrics_tile` | — |  |
| `cortex.metrics` | `GET /cortex/metrics` | `cortex.metrics_page` | tools/dashboard/templates/cortex/govern.html, tools/dashboard/templates/cortex/index.html, tools/dashboard/templates/cortex/verbs.html |  |
| `cortex.pmo` | `GET /cortex/api/pmo/status` | `cortex.api_pmo_status` | tools/dashboard/templates/cortex/pmo.html |  |
| `cortex.pmo` | `POST /cortex/api/v1/award` | `cortex.api_v1_award` | tools/dashboard/templates/cortex/pmo.html | ✓ |
| `cortex.pmo` | `POST /cortex/api/v1/bom` | `cortex.api_v1_bom` | tools/dashboard/templates/cortex/pmo.html | ✓ |
| `cortex.pmo` | `POST /cortex/api/v1/cost_volume` | `cortex.api_v1_cost_volume` | tools/dashboard/templates/cortex/pmo.html | ✓ |
| `cortex.pmo` | `POST /cortex/api/v1/dashboard` | `cortex.api_v1_dashboard` | tools/dashboard/templates/cortex/pmo.html | ✓ |
| `cortex.pmo` | `POST /cortex/api/v1/slides` | `cortex.api_v1_slides` | tools/dashboard/templates/cortex/pmo.html | ✓ |
| `cortex.pmo` | `POST /cortex/api/v1/staffing_matrix` | `cortex.api_v1_staffing_matrix` | tools/dashboard/templates/cortex/pmo.html | ✓ |
| `cortex.pmo` | `POST /cortex/api/v1/win_themes` | `cortex.api_v1_win_themes` | tools/dashboard/templates/cortex/pmo.html | ✓ |
| `cortex.pmo` | `GET /cortex/pmo` | `cortex.pmo_page` | tools/dashboard/templates/cortex/index.html | ✓ |
| `cortex.search_ask` | `GET /cortex/` | `cortex.index` | tools/dashboard/templates/cortex/airgap.html, tools/dashboard/templates/cortex/govern.html, tools/dashboard/templates/cortex/intake.html, tools/dashboard/templates/cortex/metrics.html, tools/dashboard/templates/cortex/pmo.html, tools/dashboard/templates/cortex/service_keys.html, tools/dashboard/templates/cortex/verbs.html | ✓ |
| `cortex.search_ask` | `POST /cortex/api/chat` | `cortex.api_chat` | tools/dashboard/templates/cortex/index.html | ✓ |
| `cortex.search_ask` | `POST /cortex/api/iqe-query` | `cortex.api_iqe_query` | tools/dashboard/templates/cortex/airgap.html, tools/dashboard/templates/cortex/govern.html, tools/dashboard/templates/cortex/index.html, tools/dashboard/templates/cortex/intake.html, tools/dashboard/templates/cortex/pmo.html, tools/dashboard/templates/cortex/service_keys.html, tools/dashboard/templates/cortex/verbs.html | ✓ |
| `cortex.search_ask` | `GET /cortex/api/session/<session_id>` | `cortex.api_session` | — |  |
| `cortex.search_ask` | `POST /cortex/api/v1/ask` | `cortex.api_v1_ask` | — |  |
| `cortex.search_ask` | `POST /cortex/api/v1/search` | `cortex.api_v1_search` | — |  |
| `cortex.service_keys` | `GET /cortex/api/service-keys` | `cortex.api_service_keys` | — |  |
| `cortex.service_keys` | `GET /cortex/service-keys` | `cortex.service_keys_page` | tools/dashboard/templates/cortex/index.html |  |
| `cwk.canvas` | `GET /coworkers` | `coworkers.index` | — |  |
| `cwk.canvas` | `GET /coworkers/` | `coworkers.index` | — |  |
| `cwk.canvas` | `GET /coworkers/c/<path:coworker_id>` | `coworkers.open_chat` | — |  |
| `cwk.canvas` | `GET /coworkers/status` | `coworkers.status` | — |  |
| `evidence_collectors.objective_evidence` | `GET /evidence-collectors/` | `evidence_collectors.ec_page` | tools/dashboard/templates/evidence_collectors/index.html | ✓ |
| `evidence_collectors.objective_evidence` | `GET /evidence-collectors/api/systems` | `evidence_collectors.ec_api_systems` | — |  |
| `evidence_collectors.objective_evidence` | `GET /evidence-collectors/api/systems/<sid>` | `evidence_collectors.ec_api_system` | — |  |
| `evidence_collectors.objective_evidence` | `GET /evidence-collectors/api/systems/<sid>/bundle` | `evidence_collectors.ec_api_bundle` | tools/dashboard/templates/evidence_collectors/index.html |  |
| `evidence_collectors.objective_evidence` | `POST /evidence-collectors/api/systems/<sid>/prepare` | `evidence_collectors.ec_api_prepare` | tools/dashboard/templates/evidence_collectors/index.html | ✓ |
| `evidence_collectors.objective_evidence` | `POST /evidence-collectors/api/systems/<sid>/publish` | `evidence_collectors.ec_api_publish` | tools/dashboard/templates/evidence_collectors/index.html | ✓ |
| `evidence_collectors.objective_evidence` | `GET /evidence-collectors/api/twin` | `evidence_collectors.ec_api_twin` | tools/dashboard/templates/evidence_collectors/index.html | ✓ |
| `factory_builds.signed_builds` | `GET /factory-builds/` | `factory_builds.fb_page` | tools/dashboard/templates/factory_builds/index.html | ✓ |
| `factory_builds.signed_builds` | `GET /factory-builds/api/targets` | `factory_builds.fb_api_targets` | — |  |
| `factory_builds.signed_builds` | `GET /factory-builds/api/targets/<tid>` | `factory_builds.fb_api_target` | — |  |
| `factory_builds.signed_builds` | `GET /factory-builds/api/targets/<tid>/export` | `factory_builds.fb_api_export` | tools/dashboard/templates/factory_builds/index.html |  |
| `factory_builds.signed_builds` | `POST /factory-builds/api/targets/<tid>/prepare` | `factory_builds.fb_api_prepare` | tools/dashboard/templates/factory_builds/index.html | ✓ |
| `factory_builds.signed_builds` | `POST /factory-builds/api/targets/<tid>/publish` | `factory_builds.fb_api_publish` | tools/dashboard/templates/factory_builds/index.html | ✓ |
| `factory_builds.signed_builds` | `GET /factory-builds/api/twin` | `factory_builds.fb_api_twin` | tools/dashboard/templates/factory_builds/index.html | ✓ |
| `mdz.aiify` | `GET /ai-ify/` | `aiify.index` | tools/dashboard/templates/aiify/posture.html | ✓ |
| `mdz.aiify` | `POST /ai-ify/api/generate-prd` | `aiify.api_generate_prd` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `POST /ai-ify/api/hitl-decision` | `aiify.api_hitl_decision` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `GET /ai-ify/api/intelligence-feed` | `aiify.api_intelligence_feed` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `POST /ai-ify/api/iqe-query` | `aiify.api_iqe_query` | tools/dashboard/templates/aiify/page.html, tools/dashboard/templates/aiify/posture.html | ✓ |
| `mdz.aiify` | `GET /ai-ify/api/posture-summary` | `aiify.api_posture_summary` | tools/dashboard/templates/aiify/posture.html | ✓ |
| `mdz.aiify` | `POST /ai-ify/api/posture/snapshot` | `aiify.api_posture_snapshot` | tools/dashboard/templates/aiify/posture.html | ✓ |
| `mdz.aiify` | `POST /ai-ify/api/prd-dry-run` | `aiify.api_prd_dry_run` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `POST /ai-ify/api/scan` | `aiify.api_scan` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `DELETE /ai-ify/api/scan/<int:scan_id>` | `aiify.api_delete_scan` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `GET /ai-ify/api/scan/<int:scan_id>` | `aiify.api_get_scan` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `DELETE /ai-ify/api/scan/all` | `aiify.api_delete_all_scans` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `POST /ai-ify/api/send-to-kanban` | `aiify.api_send_to_kanban` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.aiify` | `GET /ai-ify/posture` | `aiify.posture_page` | tools/dashboard/templates/aiify/page.html | ✓ |
| `mdz.cloud_native` | `GET /migration-canvas/api/modernisation/apps/<app_id>/cloud-native` | `migration_canvas.api_modernisation_cloud_native` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.exporter` | `GET /migration-canvas/api/modernisation/apps/<app_id>/bundle` | `migration_canvas.api_modernisation_bundle` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.exporter` | `GET /migration-canvas/api/modernisation/apps/<app_id>/features` | `migration_canvas.api_modernisation_features` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.exporter` | `GET /migration-canvas/api/modernisation/apps/<app_id>/ivv` | `migration_canvas.api_modernisation_survivor_ivv` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.exporter` | `POST /migration-canvas/api/modernisation/apps/<app_id>/ivv/signoff` | `migration_canvas.api_modernisation_ivv_signoff` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.exporter` | `POST /migration-canvas/api/modernisation/apps/<app_id>/processes/<process_id>/review` | `migration_canvas.api_modernisation_process_review` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.exporter` | `POST /migration-canvas/api/modernisation/icos-decisions/check` | `migration_canvas.api_modernisation_icos_check` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.processify` | `POST /workflow-canvas/api/workflows/<workflow_id>/observed-review` | `workflow_canvas.api_observed_review` | tools/dashboard/templates/workflow_canvas/processify.html | ✓ |
| `mdz.processify` | `POST /workflow-canvas/api/workflows/cleanup-processify` | `workflow_canvas.api_cleanup_processify_tasks` | tools/dashboard/templates/workflow_canvas/processify.html | ✓ |
| `mdz.processify` | `GET /workflow-canvas/api/workflows/observed-drafts` | `workflow_canvas.api_observed_drafts` | tools/dashboard/templates/workflow_canvas/processify.html | ✓ |
| `mdz.processify` | `POST /workflow-canvas/api/workflows/processify` | `workflow_canvas.api_processify_workflow` | tools/dashboard/templates/workflow_canvas/processify.html | ✓ |
| `mdz.processify` | `POST /workflow-canvas/api/workflows/save-processify` | `workflow_canvas.api_save_processify_workflow` | tools/dashboard/templates/workflow_canvas/processify.html | ✓ |
| `mdz.processify` | `GET /workflow-canvas/processify` | `workflow_canvas.processify` | — | ✓ |
| `mdz.seven_r` | `POST /api/migration/assess` | `migration_api_legacy.trigger_assessment` | tools/dashboard/templates/migration.html | ✓ |
| `mdz.seven_r` | `GET /api/migration/assessments` | `migration_api_legacy.list_assessments` | tools/dashboard/templates/migration.html | ✓ |
| `mdz.seven_r` | `POST /api/migration/assessments/<assessment_id>/strategy` | `migration_api_legacy.record_strategy` | tools/dashboard/templates/migration.html | ✓ |
| `mdz.seven_r` | `GET /api/migration/plans` | `migration_api_legacy.list_plans` | tools/dashboard/templates/migration.html | ✓ |
| `mdz.seven_r` | `GET /api/migration/stats` | `migration_api_legacy.migration_stats` | tools/dashboard/templates/migration.html | ✓ |
| `mdz.seven_r` | `GET /migration` | `migration_page` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.waves_cam` | `GET /migration-canvas/api/modernisation/apps` | `migration_canvas.api_modernisation_apps` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.waves_cam` | `POST /migration-canvas/api/modernisation/apps/<app_id>/capacity/review` | `migration_canvas.api_modernisation_capacity_review` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.waves_cam` | `GET /migration-canvas/api/modernisation/apps/<app_id>/refactor-jobs` | `migration_canvas.api_modernisation_refactor_jobs` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.waves_cam` | `GET /migration-canvas/api/modernisation/apps/<app_id>/waves` | `migration_canvas.api_modernisation_waves` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.waves_cam` | `POST /migration-canvas/api/modernisation/refactor-jobs/<job_id>/approve` | `migration_canvas.api_refactor_job_approve` | tools/dashboard/templates/migration_canvas/modernisation.html | ✓ |
| `mdz.waves_cam` | `GET /migration-canvas/modernisation` | `migration_canvas.modernisation_page` | tools/dashboard/templates/migration.html | ✓ |
| `store_publisher.signed_packages` | `GET /store-publisher/` | `store_publisher.sp_page` | tools/dashboard/templates/store_publisher/index.html | ✓ |
| `store_publisher.signed_packages` | `GET /store-publisher/api/packages` | `store_publisher.sp_api_packages` | — |  |
| `store_publisher.signed_packages` | `GET /store-publisher/api/packages/<pid>` | `store_publisher.sp_api_package` | — |  |
| `store_publisher.signed_packages` | `GET /store-publisher/api/packages/<pid>/export` | `store_publisher.sp_api_export` | tools/dashboard/templates/store_publisher/index.html |  |
| `store_publisher.signed_packages` | `POST /store-publisher/api/packages/<pid>/prepare` | `store_publisher.sp_api_prepare` | tools/dashboard/templates/store_publisher/index.html | ✓ |
| `store_publisher.signed_packages` | `POST /store-publisher/api/packages/<pid>/publish` | `store_publisher.sp_api_publish` | tools/dashboard/templates/store_publisher/index.html | ✓ |
| `store_publisher.signed_packages` | `GET /store-publisher/api/twin` | `store_publisher.sp_api_twin` | tools/dashboard/templates/store_publisher/index.html | ✓ |

### Evidence and gaps per feature

#### ACE: abort (`ace.abort`, component `ace`)

- **GUI: pass**
  - evidence: /coworker/<instance_id> linked from a nav page
  - evidence: ace_api.api_abort called from tools/dashboard/templates/coworker/_dispatch_panels.html
  - evidence: page /coworker/<instance_id> renders coworker/instance.html
  - evidence: registry entry 'ace'
- **Workflow: pass**
  - evidence: ace_api.api_abort: stop, immediate (stated reason)
  - evidence: mcp:ace_abort: stop, immediate (stated reason)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/ace.py::ACEGovernanceTwin
- **Architecture: pass**
  - evidence: PM-ACE-07 -> ace.abort -> tests/test_ace_abort.py::test_abort_stops_every_thread_and_marks_the_run_cancelled, tests/test_ace_abort.py::test_abort_is_audited_with_its_actor, tests/test_ace_abort.py::test_abort_of_an_unknown_instance_raises_and_writes_nothing, tests/test_ace_abort.py::test_the_route_stops_the_run_in_one_request_with_no_approval, tests/test_ace_abort.py::test_the_route_returns_404_for_an_unknown_instance, tests/test_ace_abort.py::test_the_mcp_tool_aborts_and_records_mcp_as_the_actor
- **AI-native: excused**
  - excused: A stop has nothing of its own to read. An agent reads the run it would stop through ace_status, a read-only tool judged under ace.runs, and ace_abort itself halts at the approval gate (default tier unknown).

#### ACE: mid-run HITL checkpoint, suggested-team confirm (`ace.hitl`, component `ace`)

- **GUI: pass**
  - evidence: /coworker/<instance_id> linked from a nav page
  - evidence: ace_api.api_hitl_resolve called from tools/dashboard/templates/coworker/hitl.html
  - evidence: ace_api.api_suggestion_confirm called from tools/dashboard/templates/includes/_chat_action_card.html
  - evidence: ace_api.api_suggestion_decline called from tools/dashboard/templates/includes/_chat_action_card.html
  - evidence: page /coworker/<instance_id> renders coworker/instance.html
  - evidence: registry entry 'ace'
- **Workflow: pass**
  - evidence: ace_api.api_hitl_resolve (name carries 'hitl') -> template decl-wft-ace-hitl-resolve (args/workflow_hitl_templates/ace-hitl-resolve.yaml)
  - evidence: ace_api.api_suggestion_confirm (name carries 'confirm') -> template decl-wft-ace-team-suggestion (args/workflow_hitl_templates/ace-team-suggestion.yaml)
  - evidence: ace_api.api_suggestion_decline (name carries 'decline') -> template decl-wft-ace-team-suggestion (args/workflow_hitl_templates/ace-team-suggestion.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/ace.py::ACEGovernanceTwin
- **Architecture: pass**
  - evidence: PM-ACE-05 -> ace.hitl, if.ace.api -> tests/test_ace_runtime.py::test_confidence_gate_fires_sets_pending_and_registers_event, tests/test_ace_runtime.py::test_hitl_resolve_wakes_thread_under_poll_interval, tests/test_ace_hitl_resolve_endpoint.py::test_hitl_resolve_endpoint_approves_confidence_gate, tests/test_ace_hitl_resolve_endpoint.py::test_hitl_resolve_endpoint_rejects_confidence_gate, tests/test_ace_hitl_resolve_endpoint.py::test_hitl_resolve_endpoint_requires_fields
  - evidence: PM-ACE-06 -> ace.hitl -> tests/test_ace_team_proposal.py::test_implicit_message_proposes_without_launching, tests/test_ace_team_proposal.py::test_declined_proposal_cannot_be_confirmed, tests/test_ace_team_proposal.py::test_confirm_is_idempotent, tests/test_ace_team_proposal.py::test_decisions_are_written_to_the_append_only_trail
- **AI-native: pass**
  - evidence: ace_checkpoints read-only, tool_authorization tier 'read'

#### ACE: role profiles (`ace.profiles`, component `ace`)

- **GUI: pass**
  - evidence: /coworker/roles linked from a nav page
  - evidence: ace_api.api_profiles_delete called from tools/dashboard/templates/coworker/roles.html
  - evidence: ace_api.api_profiles_generate called from tools/dashboard/templates/coworker/profile_new.html
  - evidence: ace_api.api_profiles_preview called from tools/dashboard/templates/coworker/profile_new.html
  - evidence: ace_api.api_profiles_suggest_name called from tools/dashboard/templates/coworker/profile_new.html
  - evidence: page /coworker/profiles/new renders coworker/profile_new.html
  - evidence: page /coworker/roles renders coworker/roles.html
  - evidence: registry entry 'ace'
- **Workflow: pass**
  - evidence: ace_api.api_profiles_delete (name carries 'delete') -> template decl-wft-ace-profile-delete (args/workflow_hitl_templates/ace-profile-delete.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/ace.py::ACEGovernanceTwin
- **Architecture: pass**
  - evidence: PM-ACE-08 -> ace.profiles.persona, if.ace.mcp -> tests/test_ace_persona_query.py::TestQueryPersona::test_uses_soul_identity_as_system_prompt, tests/test_ace_persona_query.py::TestQueryPersona::test_unknown_role_falls_back_to_generalist_system_prompt, tests/test_ace_persona_query.py::TestHandleAcePersonaQuery::test_exception_is_caught_and_returns_error_dict
  - evidence: PM-ACE-09 -> ace.profiles.persona -> tests/test_ace_persona_generator.py::test_get_or_generate_persona_requires_domain_description, tests/test_ace_persona_generator.py::test_get_or_generate_persona_reuses_cached_persona_without_a_new_llm_call
  - evidence: PM-ACE-10 -> ace.profiles.generator -> tests/test_ace_profile_generator.py::test_preview_writes_nothing_and_holds_the_model_to_the_allowlists, tests/test_ace_profile_generator.py::test_generate_writes_one_generated_role, tests/test_ace_profile_generator.py::test_generate_refuses_to_overwrite_a_builtin_role, tests/test_ace_profile_generator.py::test_a_caller_supplied_spec_is_held_to_the_same_allowlists, tests/test_ace_profile_generator.py::test_delete_removes_generated_roles_and_candidates_only, tests/test_ace_profile_generator.py::test_the_profile_routes_preview_generate_and_delete
- **AI-native: pass**
  - evidence: ace_ensure_sme mutating, approval tier 'unknown' (default_tier) halts
  - evidence: ace_persona_query read-only, tool_authorization tier 'read'

#### ACE: launch, run, rerun (`ace.runs`, component `ace`)

- **GUI: pass**
  - evidence: /coworker/<instance_id> linked from a nav page
  - evidence: /coworker/chat linked from a nav page
  - evidence: ace_api.api_ace_chat called from tools/dashboard/templates/ace/chat.html
  - evidence: ace_api.api_launch called from tools/dashboard/templates/coworker/index.html
  - evidence: ace_api.api_session_rerun called from tools/dashboard/templates/ace/session_detail.html
  - evidence: nav links /coworker
  - evidence: page /coworker renders coworker/index.html
  - evidence: page /coworker/ renders coworker/index.html
  - evidence: page /coworker/<instance_id> renders coworker/instance.html
  - evidence: page /coworker/chat renders ace/chat.html
  - evidence: page /coworker/live/<instance_id> renders ace/live.html
  - evidence: registry entry 'ace'
- **Workflow: pass**
  - evidence: ace_api.api_launch (name carries 'launch') -> template decl-wft-ace-run-launch (args/workflow_hitl_templates/ace-run-launch.yaml)
  - evidence: ace_api.api_session_rerun (name carries 'rerun') -> template decl-wft-ace-run-launch (args/workflow_hitl_templates/ace-run-launch.yaml)
  - evidence: mcp:ace_launch (name carries 'launch') -> approval tier 'unknown' (default_tier) halts in args/agent_approval_policy.yaml
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/ace.py::ACEGovernanceTwin
- **Architecture: pass**
  - evidence: PM-ACE-01 -> ace.runs, if.ace.api -> tests/test_ace_runtime.py::test_controller_launch_returns_instance_id, tests/test_ace_runtime.py::test_controller_launch_threads_role_ids_to_run
  - evidence: PM-ACE-02 -> ace.runs, if.ace.console -> tests/test_ace_session_persistence.py::test_select_by_resume_token_returns_one_row, tests/test_ace_session_persistence.py::test_append_turn_and_reload, tests/test_ace_session_persistence.py::test_duplicate_resume_token_raises, tests/test_ace_instance_page_render.py::test_instance_page_with_session_exposes_resume_token
  - evidence: PM-ACE-03 -> ace.runs, if.ace.console, if.ace.api -> tests/test_ace_instance_page_render.py::test_template_failure_on_page_route_is_500_html_not_200_json, tests/test_ace_instance_page_render.py::test_unknown_instance_is_404, tests/test_ace_instance_page_render.py::test_audit_endpoint_returns_events_array_and_count
  - evidence: PM-ACE-04 -> ace.runs -> tests/test_ace_runtime.py::test_coworker_thread_step_sequence, tests/test_ace_runtime.py::test_coworker_thread_trust_denied
- **AI-native: pass**
  - evidence: ace_launch mutating, approval tier 'unknown' (default_tier) halts
  - evidence: ace_status read-only, tool_authorization tier 'read'

#### ACE: trust (NOVA), sessions, evaluations, trends, event feed and auto-dispatch (`ace.trust_sessions`, component `ace`)

- **GUI: pass**
  - evidence: /coworker/sessions/<session_id> linked from a nav page
  - evidence: /coworker/trust linked from a nav page
  - evidence: ace_api.api_auto_dispatch_grant called from tools/dashboard/templates/coworker/_dispatch_panels.html
  - evidence: ace_api.api_auto_dispatch_resume_all called from tools/dashboard/templates/coworker/_dispatch_panels.html
  - evidence: ace_api.api_auto_dispatch_revoke called from tools/dashboard/templates/coworker/_dispatch_panels.html
  - evidence: ace_api.api_auto_dispatch_stop_all called from tools/dashboard/templates/coworker/_dispatch_panels.html
  - evidence: ace_api.api_iqe_query called from tools/dashboard/templates/ace/chat.html
  - evidence: ace_api.api_session_eval_post called from tools/dashboard/templates/ace/session_detail.html
  - evidence: nav links /coworker/sessions
  - evidence: page /coworker/evals renders ace/evals.html
  - evidence: page /coworker/evals/trends renders ace/trends.html
  - evidence: page /coworker/sessions renders ace/sessions.html
  - evidence: page /coworker/sessions/<session_id> renders ace/session_detail.html
  - evidence: page /coworker/trust renders ace/trust.html
  - evidence: registry entry 'ace'
- **Workflow: pass**
  - evidence: ace_api.api_auto_dispatch_grant (declared (tools/ace/blueprint.py: "A person turns auto-dispatch on for one role/topic")) -> template decl-wft-ace-auto-dispatch (args/workflow_hitl_templates/ace-auto-dispatch.yaml)
  - evidence: ace_api.api_auto_dispatch_resume_all (declared (tools/ace/blueprint.py: "Lift stop-all. The holder's declared step")) -> template decl-wft-ace-auto-dispatch (args/workflow_hitl_templates/ace-auto-dispatch.yaml)
  - evidence: ace_api.api_auto_dispatch_revoke: stop, immediate (stated reason)
  - evidence: ace_api.api_auto_dispatch_stop_all: stop, immediate (stated reason)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/ace.py::ACEGovernanceTwin
- **Architecture: pass**
  - evidence: PM-ACE-11 -> ace.trust, if.ace.api -> tests/test_ace_nova_state_endpoint.py::test_nova_state_trust_score_present, tests/test_ace_nova_state_endpoint.py::test_nova_state_default_trust_when_no_ledger, tests/test_ace_nova_state_endpoint.py::test_nova_state_legacy_improvement_reports_no_evidence_honestly
  - evidence: PM-ACE-12 -> ace.trust -> tests/test_ace_event_bus.py::TestPublishSubscribe::test_different_instances_isolated, tests/test_ace_event_bus.py::TestPublishSubscribe::test_full_queue_drops_event, tests/test_ace_event_bus.py::TestThreadSafety::test_concurrent_publish_and_subscribe
  - evidence: PM-ACE-13 -> ace.evaluations -> tests/test_ace_eval_routes.py::test_post_scores_a_stored_session_and_persists_it, tests/test_ace_eval_routes.py::test_an_unknown_session_is_a_404_and_writes_nothing, tests/test_ace_eval_routes.py::test_the_read_side_returns_what_was_stored, tests/test_agent_evaluator.py::TestScoreFromDB::test_score_existing_session, tests/test_agent_evaluator.py::TestSaveAndGetEval::test_save_and_retrieve, tests/test_agent_evaluator.py::TestEvalRunner::test_load_suite_parses_entries, tests/test_agent_evaluator.py::TestEvalRunner::test_run_eval_router_failure, tests/test_eval_trends.py::TestGetEvalTrends::test_fallback_query_used_when_join_fails
  - evidence: PM-ACE-14 -> ace.event_log, if.ace.api, if.ace.mcp, if.ace.dispatch_console -> tests/ace/test_event_log.py::test_emit_pending_result_recent_processed, tests/ace/test_event_log.py::test_an_undeclared_topic_is_refused, tests/ace/test_event_log.py::test_topics_are_derived_from_the_roles, tests/ace/test_event_log.py::test_the_events_route_answers_200_with_rows, tests/ace/test_event_log.py::test_the_events_route_answers_503_when_the_log_cannot_be_read, tests/ace/test_event_log.py::test_the_topics_route_answers_200_with_the_derived_map, tests/ace/test_event_log.py::test_the_completion_event_reaches_the_log, tests/ace/test_event_log.py::test_the_mcp_tools_are_declared_read_only_and_read_the_log, tests/ace/test_auto_dispatch_grant.py::test_the_coworker_page_carries_both_panels_and_calls_the_routes
  - evidence: PM-ACE-15 -> ace.dispatch -> tests/ace/test_event_dispatch.py::test_no_grant_means_a_pending_recommendation_and_no_launch, tests/ace/test_event_dispatch.py::test_a_decided_recommendation_launches_exactly_once, tests/ace/test_event_dispatch.py::test_a_declined_recommendation_launches_nothing, tests/ace/test_event_dispatch.py::test_a_withdrawn_recommendation_launches_nothing, tests/ace/test_event_dispatch.py::test_the_dispatcher_cannot_decide_its_own_recommendation, tests/ace/test_event_dispatch.py::test_a_recommendation_for_what_changed_is_withdrawn_not_launched, tests/ace/test_event_dispatch.py::test_an_undecided_recommendation_lapses, tests/test_nova_reflexion_trigger.py::test_full_round_trip_emit_recommend_decide_launch
  - evidence: PM-ACE-16 -> ace.auto_dispatch, ace.dispatch -> tests/ace/test_event_dispatch.py::test_under_a_live_grant_the_role_launches_on_auto, tests/ace/test_event_dispatch.py::test_a_revoked_grant_recommends_again, tests/ace/test_event_dispatch.py::test_stop_all_recommends_again_until_resumed, tests/ace/test_event_dispatch.py::test_an_expired_grant_recommends_and_records_its_expiry, tests/ace/test_event_dispatch.py::test_over_max_per_day_recommends, tests/ace/test_event_dispatch.py::test_falling_trust_suspends_the_grant_for_good, tests/ace/test_auto_dispatch_grant.py::test_a_grant_is_refused_and_leaves_no_decision, tests/ace/test_auto_dispatch_grant.py::test_a_grant_is_refused_below_trusted, tests/ace/test_auto_dispatch_grant.py::test_only_a_holder_may_grant, tests/ace/test_auto_dispatch_grant.py::test_a_holders_grant_is_a_decided_step_bound_to_its_payload, tests/ace/test_auto_dispatch_grant.py::test_revoke_is_immediate_with_a_reason_and_no_step, tests/ace/test_auto_dispatch_grant.py::test_stop_all_is_immediate_and_resume_needs_the_step, tests/ace/test_auto_dispatch_grant.py::test_the_role_loader_refuses_an_eligible_topic_the_role_does_not_hear
  - evidence: PM-ACE-17 -> ace.dispatch_twin -> tests/ace/test_ace_twin_dispatch.py::test_the_twin_flags_dispatch_and_grants_with_their_threads, tests/ace/test_ace_twin_dispatch.py::test_the_dispatch_checks_are_unverified_when_the_tables_are_missing
  - evidence: PM-ACE-18 -> ace.event_log -> tests/ace/test_ace_import_resolution.py::test_the_walk_finds_the_imports_that_broke, tests/ace/test_ace_import_resolution.py::test_every_imported_ace_name_resolves
- **AI-native: pass**
  - evidence: ace_auto_dispatch_grants read-only, tool_authorization tier 'read'
  - evidence: ace_events read-only, tool_authorization tier 'read'
  - evidence: nova_get_dispatch_config read-only, tool_authorization tier 'read'
  - evidence: nova_get_trust_score read-only, tool_authorization tier 'read'
  - evidence: nova_record_trust_event mutating, approval tier 'unknown' (default_tier) halts
  - evidence: nova_trust_summary read-only, tool_authorization tier 'read'

#### Engineering CoE: architecture decision records as signed, versioned records (`coe.adrs`, component `idp`)

- **GUI: pass**
  - evidence: idp.idp_api_adr_accept called from tools/dashboard/templates/idp/adrs.html
  - evidence: idp.idp_api_adr_propose called from tools/dashboard/templates/idp/adrs.html
  - evidence: idp.idp_api_adr_supersede called from tools/dashboard/templates/idp/adrs.html
  - evidence: nav links /idp/adrs
  - evidence: page /idp/adrs renders idp/adrs.html
  - evidence: registry entry 'idp'
- **Workflow: pass**
  - evidence: idp.idp_api_adr_accept (declared (tools/idp/blueprint.py: "A person, never the author, accepts a proposed ADR or returns it.")) -> template decl-wft-coe-adr-accept (args/workflow_hitl_templates/coe-adr-accept.yaml)
  - evidence: idp.idp_api_adr_supersede (declared (tools/idp/blueprint.py: "A person who wrote neither ADR supersedes an accepted ADR by its successor, or deprecates it.")) -> template decl-wft-coe-adr-supersede (args/workflow_hitl_templates/coe-adr-supersede.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/coe_standards.py::EngineeringStandardsTwin
- **Architecture: pass**
  - evidence: PM-COE-08 -> coe.adrs -> tests/test_icx_coe_02_adrs.py::test_the_migration_is_lossless, tests/test_icx_coe_02_adrs.py::test_the_flat_doc_is_generated_and_drift_fails_the_check, tests/test_icx_coe_02_adrs.py::test_every_record_validates_and_links_to_real_requirements_and_standards, tests/test_icx_coe_02_adrs.py::test_export_writes_the_record_and_the_doc_follows
  - evidence: PM-COE-09 -> coe.adrs -> tests/test_icx_coe_02_adrs.py::test_propose_accept_supersede_with_history, tests/test_icx_coe_02_adrs.py::test_the_author_is_never_the_signer, tests/test_icx_coe_02_adrs.py::test_supersede_needs_an_accepted_successor_and_neither_author, tests/test_icx_coe_02_adrs.py::test_records_are_seeded_by_the_bootstrap_identity_never_a_person, tests/test_icx_coe_02_adrs.py::test_the_ledger_is_append_only_and_hash_chained, tests/test_icx_coe_02_ambient.py::test_an_adr_lifecycle_on_the_platform_database
  - evidence: PM-COE-10 -> if.coe.records.console, coe.twin, if.coe.records.mcp -> tests/test_icx_coe_02_exemptions.py::test_the_routes_take_the_steps_and_refuse_what_they_must, tests/test_icx_coe_02_exemptions.py::test_the_pages_render_and_link_from_the_standards_page, tests/test_icx_coe_02_exemptions.py::test_the_twin_reports_expired_and_migrated_exemptions_with_threads, tests/test_icx_coe_02_exemptions.py::test_the_mcp_tools_read_and_only_draft, tests/test_icx_coe_02_adrs.py::test_the_adr_routes_and_page, tests/test_icx_coe_02_adrs.py::test_the_twin_says_unverified_for_records_not_in_the_ledger
- **AI-native: pass**
  - evidence: coe_adr_propose mutating, approval tier 'unknown' (default_tier) halts
  - evidence: coe_adrs read-only, tool_authorization tier 'read'

#### Engineering CoE: scorecards against standards, delivery evidence, conformance bundles (`coe.conformance`, component `idp`)

- **GUI: pass**
  - evidence: idp.idp_api_conformance_prepare called from tools/dashboard/templates/idp/conformance.html
  - evidence: idp.idp_api_conformance_publish called from tools/dashboard/templates/idp/conformance.html
  - evidence: nav links /idp/conformance
  - evidence: page /idp/conformance renders idp/conformance.html
  - evidence: registry entry 'idp'
- **Workflow: pass**
  - evidence: idp.idp_api_conformance_publish (declared (tools/idp/blueprint.py: "A person, never the preparer, publishes a repo's prepared bundle to ICDEV[OS], or refuses it.")) -> template decl-wft-coe-conformance-publish (args/workflow_hitl_templates/coe-conformance-publish.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/coe_standards.py::EngineeringStandardsTwin
- **Architecture: pass**
  - evidence: PM-COE-11 -> coe.conformance -> tests/test_icx_coe_03_conformance.py::test_an_adopted_scorecard_standard_is_scored_by_machine, tests/test_icx_coe_03_conformance.py::test_a_manual_evidence_standard_needs_evidence_and_never_passes, tests/test_icx_coe_03_conformance.py::test_an_expired_exemption_fails_the_standard, tests/test_icx_coe_03_conformance.py::test_a_standard_not_adopted_is_listed_not_scored, tests/test_icx_coe_03_conformance.py::test_unverified_when_nothing_is_measured
  - evidence: PM-COE-12 -> coe.delivery -> tests/test_icx_coe_03_conformance.py::test_dora_definitions_on_a_planted_history, tests/test_icx_coe_03_conformance.py::test_a_metric_with_no_data_is_unverified_never_zero, tests/test_icx_coe_03_conformance.py::test_compute_is_deterministic_and_digests_its_sources, tests/test_icx_coe_03_conformance.py::test_read_git_from_a_real_checkout, tests/test_icx_coe_03_conformance.py::test_ci_and_board_snapshots_normalise_and_refuse_garbage
  - evidence: PM-COE-13 -> coe.conformance -> tests/test_icx_coe_03_conformance.py::test_the_bundle_validates_against_the_draft_schema_and_its_digest, tests/test_icx_coe_03_conformance.py::test_the_schema_is_pinned, tests/test_icx_coe_03_conformance.py::test_the_export_is_deterministic_given_its_inputs, tests/test_icx_coe_03_conformance.py::test_the_repos_and_definitions_are_declared
  - evidence: PM-COE-14 -> coe.publish -> tests/test_icx_coe_03_conformance.py::test_publishing_is_a_persons_step_never_the_preparers, tests/test_icx_coe_03_conformance.py::test_the_ledger_is_append_only_and_hash_chained, tests/test_icx_coe_03_ambient.py::test_a_bundle_prepared_and_published_on_the_platform_database
  - evidence: PM-COE-15 -> if.coe.conformance.console, coe.twin, if.coe.conformance.mcp -> tests/test_icx_coe_03_conformance.py::test_the_routes_and_the_page, tests/test_icx_coe_03_conformance.py::test_the_twin_observes_bundles_with_threads, tests/test_icx_coe_03_conformance.py::test_the_twin_says_unverified_when_the_ledger_cannot_be_read, tests/test_icx_coe_03_conformance.py::test_the_mcp_tools_read_and_only_prepare
- **AI-native: pass**
  - evidence: coe_conformance read-only, tool_authorization tier 'read'
  - evidence: coe_conformance_prepare mutating, approval tier 'unknown' (default_tier) halts

#### Engineering CoE: scorecard exemptions that expire (`coe.exemptions`, component `idp`)

- **GUI: pass**
  - evidence: idp.idp_api_exemption_decide called from tools/dashboard/templates/idp/exemptions.html
  - evidence: idp.idp_api_exemption_renew called from tools/dashboard/templates/idp/exemptions.html
  - evidence: idp.idp_api_exemption_renewal called from tools/dashboard/templates/idp/exemptions.html
  - evidence: idp.idp_api_exemption_request called from tools/dashboard/templates/idp/exemptions.html
  - evidence: idp.idp_api_exemption_revoke called from tools/dashboard/templates/idp/exemptions.html
  - evidence: nav links /idp/exemptions
  - evidence: page /idp/exemptions renders idp/exemptions.html
  - evidence: registry entry 'idp'
- **Workflow: pass**
  - evidence: idp.idp_api_exemption_decide (declared (tools/idp/blueprint.py: "A person, never the requester, grants an exemption request or denies it.")) -> template decl-wft-coe-exemption-grant (args/workflow_hitl_templates/coe-exemption-grant.yaml)
  - evidence: idp.idp_api_exemption_renew (declared (tools/idp/blueprint.py: "A person, never the requester or the asker, renews an exemption or refuses.")) -> template decl-wft-coe-exemption-renew (args/workflow_hitl_templates/coe-exemption-renew.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/coe_standards.py::EngineeringStandardsTwin
- **Architecture: pass**
  - evidence: PM-COE-06 -> coe.exemptions -> tests/test_icx_coe_02_exemptions.py::test_a_request_without_an_expiry_is_refused, tests/test_icx_coe_02_exemptions.py::test_an_expiry_must_be_after_today_and_within_the_horizon, tests/test_icx_coe_02_exemptions.py::test_scopes_resolve_and_link_the_standards_they_waive, tests/test_icx_coe_02_exemptions.py::test_an_expired_exemption_fails_the_scorecard_and_the_ci_check, tests/test_icx_coe_02_exemptions.py::test_a_config_grant_or_auto_approve_fails_the_check
  - evidence: PM-COE-07 -> coe.exemptions -> tests/test_icx_coe_02_exemptions.py::test_the_requester_is_never_the_approver_and_a_step_is_bound_to_the_digest, tests/test_icx_coe_02_exemptions.py::test_renewal_is_a_persons_step_and_nothing_renews_by_itself, tests/test_icx_coe_02_exemptions.py::test_revoking_is_a_stop_with_a_reason, tests/test_icx_coe_02_exemptions.py::test_the_log_is_append_only_and_hash_chained, tests/test_icx_coe_02_exemptions.py::test_legacy_exemptions_are_migrated_by_the_bootstrap_identity_never_granted, tests/test_icx_coe_02_exemptions.py::test_rows_carry_the_writers_label_and_a_person_reads_back_their_request, tests/test_icx_coe_02_ambient.py::test_an_exemption_lifecycle_on_the_platform_database
  - evidence: PM-COE-10 -> if.coe.records.console, coe.twin, if.coe.records.mcp -> tests/test_icx_coe_02_exemptions.py::test_the_routes_take_the_steps_and_refuse_what_they_must, tests/test_icx_coe_02_exemptions.py::test_the_pages_render_and_link_from_the_standards_page, tests/test_icx_coe_02_exemptions.py::test_the_twin_reports_expired_and_migrated_exemptions_with_threads, tests/test_icx_coe_02_exemptions.py::test_the_mcp_tools_read_and_only_draft, tests/test_icx_coe_02_adrs.py::test_the_adr_routes_and_page, tests/test_icx_coe_02_adrs.py::test_the_twin_says_unverified_for_records_not_in_the_ledger
- **AI-native: pass**
  - evidence: coe_exemption_request mutating, approval tier 'unknown' (default_tier) halts
  - evidence: coe_exemptions read-only, tool_authorization tier 'read'

#### Engineering CoE: golden paths, a new service conformant by construction (`coe.golden_paths`, component `idp`)

- **GUI: pass**
  - evidence: idp.idp_api_golden_path_drift called from tools/dashboard/templates/idp/golden_paths.html
  - evidence: idp.idp_api_golden_path_propose called from tools/dashboard/templates/idp/golden_paths.html
  - evidence: idp.idp_api_golden_path_publish called from tools/dashboard/templates/idp/golden_paths.html
  - evidence: idp.idp_api_golden_path_retire called from tools/dashboard/templates/idp/golden_paths.html
  - evidence: nav links /idp/golden-paths
  - evidence: page /idp/golden-paths renders idp/golden_paths.html
  - evidence: registry entry 'idp'
- **Workflow: pass**
  - evidence: idp.idp_api_golden_path_publish (declared (tools/idp/blueprint.py: "A person, never the version's author, publishes a proposed golden path version, or refuses it.")) -> template decl-wft-coe-golden-path-publish (args/workflow_hitl_templates/coe-golden-path-publish.yaml)
  - evidence: idp.idp_api_golden_path_retire (declared (tools/idp/blueprint.py: "A person, never the version's author, retires a published golden path version, or keeps it.")) -> template decl-wft-coe-golden-path-retire (args/workflow_hitl_templates/coe-golden-path-retire.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/coe_standards.py::EngineeringStandardsTwin
- **Architecture: pass**
  - evidence: PM-COE-16 -> coe.golden_paths -> tests/test_icx_coe_04_golden_paths.py::test_the_paths_declare_every_standard_in_force_derived_from_the_register, tests/test_icx_coe_04_golden_paths.py::test_adopting_a_standard_that_applies_puts_the_path_behind_and_ci_fails, tests/test_icx_coe_04_golden_paths.py::test_a_standard_that_does_not_apply_or_a_version_change_is_judged_exactly, tests/test_icx_coe_04_golden_paths.py::test_the_ledger_check_judges_the_version_in_use_as_the_page_does
  - evidence: PM-COE-17 -> coe.golden_paths -> tests/test_icx_coe_04_golden_paths.py::test_scaffold_service_renders_every_piece_and_registers_it_as_text, tests/test_icx_coe_04_golden_paths.py::test_the_scaffold_finds_its_repo_in_a_checkout_named_icdev_and_from_the_mirror
  - evidence: PM-COE-18 -> coe.golden_drift -> tests/test_icx_coe_04_golden_paths.py::test_the_drift_check_passes_the_scaffold_machine_pass_manual_needs_evidence, tests/test_icx_coe_04_golden_paths.py::test_a_machine_standard_the_scaffold_does_not_meet_is_drift_never_pass, tests/test_icx_coe_04_golden_paths.py::test_the_scorecard_grades_another_tree_only_while_rooted
  - evidence: PM-COE-19 -> coe.golden_publish -> tests/test_icx_coe_04_golden_paths.py::test_propose_drift_and_publish_by_another_person_when_the_gate_holds, tests/test_icx_coe_04_golden_paths.py::test_the_gate_blocks_a_failed_or_stale_drift_check_and_a_version_behind, tests/test_icx_coe_04_golden_paths.py::test_an_agent_only_drafts_and_retiring_is_a_persons_step_never_the_authors, tests/test_icx_coe_04_golden_paths.py::test_the_ledger_is_append_only_and_hash_chained, tests/test_icx_coe_04_golden_paths.py::test_seeds_are_recorded_by_the_bootstrap_identity_never_a_person, tests/test_icx_coe_04_ambient.py::test_a_path_version_proposed_drift_checked_and_published_on_the_platform_database
  - evidence: PM-COE-20 -> if.coe.golden_paths.console, coe.golden_twin, if.coe.golden_paths.mcp -> tests/test_icx_coe_04_golden_paths.py::test_the_routes_and_the_page, tests/test_icx_coe_04_golden_paths.py::test_the_twin_observes_paths_with_threads_and_replays, tests/test_icx_coe_04_golden_paths.py::test_the_twin_says_unverified_when_the_ledger_cannot_be_read, tests/test_icx_coe_04_golden_paths.py::test_the_mcp_tools_read_and_only_propose
- **AI-native: pass**
  - evidence: coe_golden_path_propose mutating, approval tier 'unknown' (default_tier) halts
  - evidence: coe_golden_paths read-only, tool_authorization tier 'read'

#### Engineering CoE: standards register with a lifecycle (`coe.standards`, component `idp`)

- **GUI: pass**
  - evidence: idp.idp_api_standard_adopt called from tools/dashboard/templates/idp/standards.html
  - evidence: idp.idp_api_standard_deprecate called from tools/dashboard/templates/idp/standards.html
  - evidence: idp.idp_api_standard_propose called from tools/dashboard/templates/idp/standards.html
  - evidence: idp.idp_api_standard_review called from tools/dashboard/templates/idp/standards.html
  - evidence: nav links /idp/standards
  - evidence: page /idp/standards renders idp/standards.html
  - evidence: registry entry 'idp'
- **Workflow: pass**
  - evidence: idp.idp_api_standard_adopt (declared (tools/idp/blueprint.py: "A steward, never the author, signs a reviewed standard into force, or refuses it.")) -> template decl-wft-coe-standard-adopt (args/workflow_hitl_templates/coe-standard-adopt.yaml)
  - evidence: idp.idp_api_standard_deprecate (declared (tools/idp/blueprint.py: "A steward, never the author, retires the standard in force, with a reason.")) -> template decl-wft-coe-standard-deprecate (args/workflow_hitl_templates/coe-standard-deprecate.yaml)
  - evidence: idp.idp_api_standard_review (declared (tools/idp/blueprint.py: "A steward, never the author, endorses a proposed standard or returns it.")) -> template decl-wft-coe-standard-review (args/workflow_hitl_templates/coe-standard-review.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/coe_standards.py::EngineeringStandardsTwin
- **Architecture: pass**
  - evidence: PM-COE-01 -> coe.register -> tests/test_icx_coe_01_standards.py::test_the_register_validates_and_seeds_by_reference, tests/test_icx_coe_01_standards.py::test_every_owner_role_is_a_dashboard_role, tests/test_icx_coe_01_standards.py::test_the_citation_catalog_is_untouched_and_its_reader_still_validates, tests/test_icx_coe_01_standards.py::test_a_malformed_draft_is_refused
  - evidence: PM-COE-02 -> coe.check, coe.lifecycle -> tests/test_icx_coe_01_standards.py::test_a_check_resolves_only_when_it_exists, tests/test_icx_coe_01_standards.py::test_adoption_refuses_a_standard_without_a_check, tests/test_icx_coe_01_standards.py::test_a_seed_without_a_check_cannot_enter_adopted
  - evidence: PM-COE-03 -> coe.lifecycle -> tests/test_icx_coe_01_standards.py::test_propose_review_adopt_deprecate_with_history, tests/test_icx_coe_01_standards.py::test_the_author_is_never_the_reviewer_or_the_signer, tests/test_icx_coe_01_standards.py::test_a_steward_role_is_needed_and_a_reason, tests/test_icx_coe_01_standards.py::test_nothing_advances_out_of_order_and_a_step_is_bound_to_the_digest, tests/test_icx_coe_01_standards.py::test_an_agent_only_drafts, tests/test_icx_coe_01_standards.py::test_a_new_version_supersedes_the_one_in_force, tests/test_icx_coe_01_ambient.py::test_the_lifecycle_on_the_platform_database
  - evidence: PM-COE-04 -> coe.lifecycle, coe.register -> tests/test_icx_coe_01_standards.py::test_the_ledger_is_append_only_and_a_forged_row_breaks_the_chain, tests/test_icx_coe_01_standards.py::test_two_events_cannot_follow_the_same_one, tests/test_icx_coe_01_standards.py::test_seeds_are_recorded_by_the_bootstrap_identity_never_a_person, tests/test_icx_coe_01_ambient.py::test_the_lifecycle_on_the_platform_database
  - evidence: PM-COE-05 -> if.coe.console, coe.twin, if.coe.mcp -> tests/test_icx_coe_01_standards.py::test_the_routes_take_the_lifecycle_and_refuse_what_they_must, tests/test_icx_coe_01_standards.py::test_the_page_renders_the_register_and_its_controls, tests/test_icx_coe_01_standards.py::test_the_twin_observes_with_threads_that_resolve_and_replays, tests/test_icx_coe_01_standards.py::test_the_twin_says_unverified_when_the_ledger_cannot_be_read, tests/test_icx_coe_01_standards.py::test_the_mcp_tools_read_and_only_draft, tests/test_icx_coe_01_standards.py::test_the_cli_validates_and_lists
- **AI-native: pass**
  - evidence: coe_standard_propose mutating, approval tier 'unknown' (default_tier) halts
  - evidence: coe_standards read-only, tool_authorization tier 'read'

#### Feature conformance audit (this tool) (`conformance.audit`, component `twin_observatory`)

- **GUI: pass**
  - evidence: /twin-observatory/conformance linked from a nav page
  - evidence: page /twin-observatory/conformance renders twin_observatory/conformance.html
  - evidence: registry entry 'twin_observatory'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/conformance.py::ConformanceAuditTwin
- **Architecture: pass**
  - evidence: PM-CNF-01 -> conformance.audit -> tests/test_feature_conformance_audit.py::test_the_baseline_meets_all_five_rules, tests/test_feature_conformance_audit.py::test_architecture_an_orphan_element_and_a_missing_test_are_gaps, tests/test_feature_conformance_audit.py::test_a_stated_reason_excuses_only_its_own_rule, tests/test_feature_conformance_audit.py::test_a_reason_that_excuses_nothing_is_stale
  - evidence: PM-CNF-02 -> conformance.audit -> tests/test_feature_conformance_audit.py::test_two_runs_and_a_shuffled_url_map_are_byte_identical, tests/test_feature_conformance_audit.py::test_check_fails_on_a_stale_doc_snapshot_or_census, tests/test_feature_conformance_audit.py::test_the_census_ceiling_may_not_be_exceeded
  - evidence: PM-CNF-03 -> conformance.audit, if.conformance.console, if.conformance.mcp -> tests/test_feature_conformance_audit.py::test_the_console_page_and_api_render_the_snapshot, tests/test_feature_conformance_audit.py::test_the_mcp_tool_is_declared_read_only_and_serves_the_snapshot
  - evidence: PM-CNF-04 -> platform_model.seeder -> tests/test_platform_model.py::test_the_shipped_model_validates, tests/test_platform_model.py::test_an_invented_test_name_is_refused, tests/test_platform_model.py::test_an_untested_requirement_is_a_gap_not_a_pass, tests/test_platform_model.py::test_seeded_thread_has_no_orphans_or_gaps_for_traced_features, tests/test_platform_model.py::test_mcp_trace_forward_and_backward_resolve_a_requirement
  - evidence: PM-CNF-05 -> if.lineage.platform_model, if.conformance.mcp -> tests/test_platform_model.py::test_lineage_route_and_page_show_the_trace, tests/test_platform_model.py::test_mcp_platform_model_trace_is_read_only
  - evidence: PM-CNF-08 -> conformance.route_ids, conformance.audit, if.conformance.console -> tests/test_feature_conformance_audit.py::test_a_page_that_builds_a_path_for_a_route_is_not_credited, tests/test_feature_conformance_audit.py::test_a_hand_written_api_path_in_an_audited_file_fails_check, tests/test_feature_conformance_audit.py::test_a_stale_route_table_or_an_unknown_id_fails_check, tests/test_feature_conformance_audit.py::test_the_e2e_gate_fails_on_a_named_write_no_page_sent, tests/test_feature_conformance_audit.py::test_the_e2e_gate_fails_on_an_overclaiming_record_or_a_stray_request, tests/test_icx_cnf_08_route_ids.py::test_no_audited_file_hand_writes_an_api_path, tests/test_icx_cnf_08_route_ids.py::test_every_credited_write_was_seen_sent_by_a_page
- **AI-native: pass**
  - evidence: feature_conformance read-only, tool_authorization tier 'read'

#### Cortex: agent launch (`cortex.agent_launch`, component `cortex`)

- **GUI: pass**
  - evidence: cortex.api_chat called from tools/dashboard/templates/cortex/index.html
  - evidence: cortex.api_v1_agent machine surface (stated reason)
  - evidence: nav links /cortex
  - evidence: page /cortex/ renders cortex/index.html
  - evidence: registry entry 'cortex'
- **Workflow: pass**
  - evidence: cortex.api_chat (declared (tools/cortex/blueprint.py: "confirm to proceed")) -> template decl-wft-cortex-agent-launch (args/workflow_hitl_templates/cortex-agent-launch.yaml)
  - evidence: cortex.api_v1_agent (declared (tools/cortex/rest_v1.py: "it is the one endpoint here that")) -> template decl-wft-cortex-agent-launch (args/workflow_hitl_templates/cortex-agent-launch.yaml)
  - evidence: mcp:cortex_agent_launch (name carries 'launch') -> approval tier 'unknown' (default_tier) halts in args/agent_approval_policy.yaml
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-10 -> cortex.agent_launch, if.cortex.rest_v1 -> tests/cortex/test_rest_agent.py::test_agent_is_not_in_the_default_grant, tests/cortex/test_rest_agent.py::test_scope_missing_403, tests/cortex/test_rest_agent.py::test_identity_comes_from_the_session_not_the_body, tests/cortex/test_rest_agent.py::test_tools_and_handlers_are_never_forwarded
  - evidence: PM-CTX-11 -> cortex.agent_launch -> tests/cortex/test_chat_routing.py::TestChatRouting::test_agent_intent_gated_behind_confirm, tests/cortex/test_agent_confirm.py::test_unconfirmed_agent_does_not_launch, tests/cortex/test_agent_confirm.py::test_confirmed_agent_launches_via_governed_facade, tests/cortex/test_agent_confirm.py::test_proposal_names_the_actual_roster
- **AI-native: pass**
  - evidence: cortex_agent_launch mutating, approval tier 'unknown' (default_tier) halts
  - evidence: cortex_agent_launches read-only, tool_authorization tier 'read'

#### Cortex: air-gap guard and health (`cortex.airgap_guard`, component `cortex`)

- **GUI: pass**
  - evidence: /cortex/airgap linked from a nav page
  - evidence: page /cortex/airgap renders cortex/airgap.html
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-22 -> cortex.airgap_guard -> tests/cortex/test_summarize_routing_airgap.py::test_the_airgap_guard_can_see_this_function, tests/cortex/test_summarize_routing_airgap.py::test_airgap_context_threads_exclude_model_ids, tests/cortex/test_summarize_routing_airgap.py::test_every_declared_cortex_routing_function_exists_under_routing
  - evidence: PM-CTX-23 -> cortex.airgap_guard, if.cortex.rest_v1 -> tests/cortex/test_rest_scopes.py::test_health_requires_no_auth
- **AI-native: pass**
  - evidence: cortex_airgap_posture read-only, tool_authorization tier 'read'

#### Cortex: resolution findings (`cortex.findings`, component `cortex`)

- **GUI: pass**
  - evidence: dic.api_explorer_cortex_finding_adjudicate called from tools/dashboard/templates/document_intelligence/explorer.html
  - evidence: nav links /document-intelligence/explorer
  - evidence: page /document-intelligence/explorer renders document_intelligence/explorer.html
  - evidence: registry entry 'cortex'
- **Workflow: pass**
  - evidence: dic.api_explorer_cortex_finding_adjudicate (name carries 'adjudicate') -> template decl-wft-cortex-finding-adjudicate (args/workflow_hitl_templates/cortex-finding-adjudicate.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-12 -> cortex.findings -> tests/cortex/test_finding_store.py::TestNoSilentWinner::test_no_winner_column_exists, tests/cortex/test_finding_store.py::TestNoSilentWinner::test_both_values_are_kept, tests/cortex/test_finding_store.py::TestConflictRoundTrip::test_each_side_keeps_its_own_source_and_as_of
  - evidence: PM-CTX-13 -> cortex.findings -> tests/cortex/test_explorer_conflict_render.py::TestNoSilentResolution::test_the_page_says_it_is_unresolved_by_design, tests/cortex/test_explorer_conflict_render.py::TestNoSilentResolution::test_neither_side_is_hidden, tests/cortex/test_finding_store.py::TestGapBrowsing::test_all_gaps_are_browsable
- **AI-native: pass**
  - evidence: cortex_findings_list read-only, tool_authorization tier 'read'

#### Cortex: govern (TRUST chain) (`cortex.govern`, component `cortex`)

- **GUI: pass**
  - evidence: /cortex/govern linked from a nav page
  - evidence: cortex.api_v1_govern called from tools/dashboard/templates/cortex/govern.html
  - evidence: page /cortex/govern renders cortex/govern.html
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 1 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-08 -> cortex.govern, if.cortex.rest_v1 -> tests/cortex/test_rest_api.py::test_govern_returns_report, tests/cortex/test_rest_api.py::test_governance_block_returns_403, tests/cortex/test_rest_single_governance.py::test_the_govern_endpoint_still_runs_the_chain_once
  - evidence: PM-CTX-09 -> cortex.govern -> tests/cortex/test_governance_kg_gate.py, tests/cortex/test_governance_timing.py
- **AI-native: pass**
  - evidence: cortex_govern mutating, approval tier 'unknown' (default_tier) halts
  - evidence: cortex_trust_chain read-only, tool_authorization tier 'read'

#### Cortex: resolve, complete, reason, classify, extract (`cortex.governed_verbs`, component `cortex`)

- **GUI: pass**
  - evidence: /cortex/verbs linked from a nav page
  - evidence: cortex.api_v1_classify called from tools/dashboard/templates/cortex/verbs.html
  - evidence: cortex.api_v1_complete called from tools/dashboard/templates/cortex/verbs.html
  - evidence: cortex.api_v1_extract called from tools/dashboard/templates/cortex/verbs.html
  - evidence: cortex.api_v1_reason called from tools/dashboard/templates/cortex/verbs.html
  - evidence: cortex.api_v1_resolve called from tools/dashboard/templates/cortex/verbs.html
  - evidence: page /cortex/verbs renders cortex/verbs.html
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 5 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-04 -> cortex.governed_verbs, if.cortex.rest_v1 -> tests/cortex/test_rest_api.py::test_complete_is_governed, tests/cortex/test_rest_api.py::test_reason_is_governed, tests/cortex/test_rest_api.py::test_classify_is_governed, tests/cortex/test_rest_api.py::test_extract_is_governed, tests/cortex/test_rest_api.py::test_a_schema_refusal_is_422_not_500, tests/cortex/test_rest_single_governance.py::test_one_rest_call_enters_the_trust_chain_once
  - evidence: PM-CTX-05 -> cortex.governed_verbs -> tests/cortex/test_resolve_facade.py::TestVerdictIsDeterministic::test_verdict_comes_from_pack_evaluate, tests/cortex/test_resolve_facade.py::TestVerdictIsDeterministic::test_no_llm_call_is_made_at_all, tests/cortex/test_resolve_facade.py::TestAuditAndProvenance::test_a_resolution_audits_with_a_provenance_id
  - evidence: PM-CTX-06 -> cortex.governed_verbs -> tests/cortex/test_extract_validation.py::test_non_conforming_output_raises, tests/cortex/test_extract_validation.py::test_a_partly_valid_object_is_never_returned, tests/cortex/test_extract_validation.py::test_repair_stops_at_one_attempt
  - evidence: PM-CTX-07 -> cortex.governed_verbs, if.cortex.mcp -> tests/cortex/test_mcp_tools.py::test_complete_serializes_result_with_governance, tests/cortex/test_mcp_tools.py::test_complete_surfaces_governance_block, tests/cortex/test_mcp_tools.py::test_reason_serializes_result_with_mode
- **AI-native: pass**
  - evidence: cortex_classify mutating, approval tier 'unknown' (default_tier) halts
  - evidence: cortex_complete mutating, approval tier 'unknown' (default_tier) halts
  - evidence: cortex_extract mutating, approval tier 'unknown' (default_tier) halts
  - evidence: cortex_reason mutating, approval tier 'unknown' (default_tier) halts
  - evidence: cortex_resolve read-only, tool_authorization tier 'read'

#### Cortex: intake bridge (`cortex.intake`, component `cortex`)

- **GUI: pass**
  - evidence: /cortex/intake linked from a nav page
  - evidence: cortex.api_v1_intake_session_create called from tools/dashboard/templates/cortex/intake.html
  - evidence: cortex.api_v1_intake_turn called from tools/dashboard/templates/cortex/intake.html
  - evidence: page /cortex/intake renders cortex/intake.html
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 2 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-18 -> cortex.intake, if.cortex.rest_v1 -> tests/cortex/test_rest_intake.py::test_create_seeds_verbatim_ask_as_first_turn, tests/cortex/test_rest_intake.py::test_turn_other_tenant_session_404, tests/cortex/test_rest_intake.py::test_get_session_whitelists_fields, tests/cortex/test_rest_intake.py::test_scope_missing_403
- **AI-native: pass**
  - evidence: cortex_intake_sessions read-only, tool_authorization tier 'read'

#### Cortex: metrics (`cortex.metrics`, component `cortex`)

- **GUI: pass**
  - evidence: /cortex/metrics linked from a nav page
  - evidence: page /cortex/metrics renders cortex/metrics.html
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-21 -> cortex.metrics -> tests/cortex/test_metrics.py::test_missing_table_reports_unavailable, tests/cortex/test_metrics.py::test_healthy_but_empty_window_reports_idle, tests/cortex/test_metrics.py::test_counts_stay_exact_when_detail_is_truncated, tests/cortex/test_metrics.py::test_metrics_page_discloses_a_truncated_detail_read
- **AI-native: pass**
  - evidence: cortex_metrics read-only, tool_authorization tier 'read'

#### Cortex: PMO verbs (slides, win themes, staffing, cost, dashboard, award, BOM) (`cortex.pmo`, component `cortex`)

- **GUI: pass**
  - evidence: /cortex/pmo linked from a nav page
  - evidence: cortex.api_v1_award called from tools/dashboard/templates/cortex/pmo.html
  - evidence: cortex.api_v1_bom called from tools/dashboard/templates/cortex/pmo.html
  - evidence: cortex.api_v1_cost_volume called from tools/dashboard/templates/cortex/pmo.html
  - evidence: cortex.api_v1_dashboard called from tools/dashboard/templates/cortex/pmo.html
  - evidence: cortex.api_v1_slides called from tools/dashboard/templates/cortex/pmo.html
  - evidence: cortex.api_v1_staffing_matrix called from tools/dashboard/templates/cortex/pmo.html
  - evidence: cortex.api_v1_win_themes called from tools/dashboard/templates/cortex/pmo.html
  - evidence: page /cortex/pmo renders cortex/pmo.html
  - evidence: registry entry 'cortex'
- **Workflow: pass**
  - evidence: cortex.api_v1_award (name carries 'award') -> template decl-wft-cortex-award (args/workflow_hitl_templates/cortex-award.yaml)
  - evidence: cortex.api_v1_bom (declared (tools/cortex/rest_v1.py: "Only a human's designation binds")) -> template decl-wft-cortex-bom-designation (args/workflow_hitl_templates/cortex-bom-designation.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-14 -> cortex.pmo.award, if.cortex.rest_v1 -> tests/cortex/test_rest_award.py::test_award_scope_exists_but_is_not_granted_by_default, tests/cortex/test_rest_award.py::test_pricing_a_bid_does_not_let_you_declare_it_won, tests/cortex/test_rest_award.py::test_award_requires_authentication
  - evidence: PM-CTX-15 -> cortex.pmo.evidence -> tests/cortex/test_rest_win_themes.py::test_uncited_theme_is_refused_not_stored, tests/cortex/test_rest_staffing_matrix.py::test_an_unevidenced_person_is_REFUSED_not_stored, tests/cortex/test_rest_cost_volume.py::test_an_unrated_lcat_refuses_the_price_rather_than_guessing
  - evidence: PM-CTX-16 -> cortex.pmo.render -> tests/cortex/test_rest_slides.py::test_image_path_never_reaches_the_builder, tests/cortex/test_rest_dashboard.py::test_a_path_bearing_key_NEVER_reaches_the_renderer, tests/cortex/test_rest_dashboard.py::test_the_caller_cannot_mark_its_own_export_DOWN
  - evidence: PM-CTX-17 -> cortex.pmo.bom -> tests/cortex/test_rest_bom.py::test_the_route_needs_a_caller_and_the_bom_scope, tests/cortex/test_rest_bom.py::test_an_undeclared_tier_is_proposed_never_self_approved, tests/cortex/test_rest_bom.py::test_a_service_keys_declaration_is_only_a_proposal, tests/cortex/test_rest_bom.py::test_only_a_persons_declaration_binds, tests/cortex/test_rest_bom.py::test_no_documents_is_a_400_naming_the_defect, tests/bom/test_cortex_bom.py::TestTheScope::test_it_is_never_in_the_default_grant, tests/bom/test_cortex_bom.py::TestSilenceIsNeverConfirmation::test_what_the_caller_omits_is_PROPOSED_and_says_so, tests/bom/test_cortex_bom.py::TestTheSecurityPosture::test_nothing_the_caller_uploads_is_persisted
- **AI-native: pass**
  - evidence: cortex_pmo_status read-only, tool_authorization tier 'read'

#### Cortex: search and ask (`cortex.search_ask`, component `cortex`)

- **GUI: pass**
  - evidence: cortex.api_chat called from tools/dashboard/templates/cortex/index.html
  - evidence: cortex.api_iqe_query called from tools/dashboard/templates/cortex/airgap.html
  - evidence: cortex.api_v1_ask machine surface (stated reason)
  - evidence: cortex.api_v1_search machine surface (stated reason)
  - evidence: nav links /cortex
  - evidence: page /cortex/ renders cortex/index.html
  - evidence: registry entry 'cortex'
- **Workflow: pass**
  - evidence: cortex.api_chat: not a decision (stated reason)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-01 -> cortex.search_ask, if.cortex.rest_v1 -> tests/cortex/test_rest_api.py::test_search_returns_normalized_rows, tests/cortex/test_rest_api.py::test_ask_returns_cortex_result, tests/cortex/test_rest_api.py::test_ask_query_blocked_returns_403, tests/cortex/test_rest_api.py::test_ask_unanswerable_returns_422
  - evidence: PM-CTX-02 -> cortex.search_ask, if.cortex.console -> tests/cortex/test_chat_routing.py::TestChatRouting::test_search_message_calls_cortex_search, tests/cortex/test_chat_routing.py::TestChatRouting::test_data_question_calls_cortex_ask, tests/cortex/test_chat_routing.py::TestChatRouting::test_explicit_mode_overrides_routing, tests/cortex/test_chat_routing.py::TestChatRouting::test_facade_error_degrades_not_500, tests/cortex/test_blueprint_routes.py::TestPageRoutes::test_index_returns_200_and_branding
  - evidence: PM-CTX-03 -> cortex.search_ask, if.cortex.mcp -> tests/cortex/test_mcp_tools.py::test_registry_lists_all_cortex_tools, tests/cortex/test_mcp_tools.py::test_search_round_trips_results, tests/cortex/test_mcp_tools.py::test_search_requires_query, tests/cortex/test_mcp_tools.py::test_search_wraps_backend_error
- **AI-native: pass**
  - evidence: cortex_ask read-only, tool_authorization tier 'read'
  - evidence: cortex_search read-only, tool_authorization tier 'read'

#### Cortex: service keys (`cortex.service_keys`, component `cortex`)

- **GUI: pass**
  - evidence: /cortex/service-keys linked from a nav page
  - evidence: page /cortex/service-keys renders cortex/service_keys.html
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cortex.py::CortexGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CTX-19 -> cortex.service_keys -> tests/cortex/test_service_keys.py::test_raw_key_never_stored, tests/cortex/test_service_keys.py::test_revoked_key_rejected, tests/cortex/test_service_keys.py::test_tenant_never_from_request, tests/cortex/test_service_keys.py::test_classification_clamped_to_ceiling
  - evidence: PM-CTX-20 -> cortex.service_keys, if.cortex.rest_v1 -> tests/cortex/test_rest_scopes.py::test_scope_missing_403, tests/cortex/test_rest_scopes.py::test_unauthenticated_401, tests/cortex/test_rest_api.py::test_unauthenticated_requests_rejected, tests/security/test_canvas_guard_service_key.py::test_scope_enforcement_still_exists_for_the_exempted_principal
- **AI-native: pass**
  - evidence: cortex_service_keys read-only, tool_authorization tier 'read'

#### Co-Workers canvas (`cwk.canvas`, component `cwk`)

- **GUI: pass**
  - evidence: nav links /coworkers
  - evidence: page /coworkers renders coworkers/index.html
  - evidence: page /coworkers/ renders coworkers/index.html
  - evidence: registry entry 'cwk'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/cwk.py::CoWorkersGovernanceTwin
- **Architecture: pass**
  - evidence: PM-CWK-01 -> if.cwk.console -> tests/test_component_registry.py::test_get_iqe_path_canvas_cwk_ace_collision_rule
  - evidence: PM-CWK-02 -> cwk.canvas, if.cwk.console -> tests/test_coworkers_canvas.py::test_the_roster_is_personas_then_ace_roles_from_config, tests/test_coworkers_canvas.py::test_a_missing_source_contributes_nothing, tests/test_coworkers_canvas.py::test_the_chat_link_is_a_relative_chat_url_for_that_coworker, tests/test_coworkers_canvas.py::test_the_roster_page_lists_every_coworker_with_its_chat_link, tests/test_coworkers_canvas.py::test_a_roster_that_cannot_load_is_empty_not_a_500, tests/test_coworkers_canvas.py::test_open_chat_redirects_into_the_shared_chat_or_404s, tests/test_coworkers_canvas.py::test_status_counts_the_roster_by_kind
- **AI-native: pass**
  - evidence: coworkers_roster read-only, tool_authorization tier 'read'

#### Evidence collectors: read-only customer sources, objective evidence for ICDEV[OS] (`evidence_collectors.objective_evidence`, component `evidence_collectors`)

- **GUI: pass**
  - evidence: evidence_collectors.ec_api_prepare called from tools/dashboard/templates/evidence_collectors/index.html
  - evidence: evidence_collectors.ec_api_publish called from tools/dashboard/templates/evidence_collectors/index.html
  - evidence: nav links /evidence-collectors
  - evidence: page /evidence-collectors/ renders evidence_collectors/index.html
  - evidence: registry entry 'evidence_collectors'
- **Workflow: pass**
  - evidence: evidence_collectors.ec_api_publish (declared (tools/evidence_collectors/blueprint.py: "A person, never the preparer, publishes a system's prepared objective-evidence report to ICDEV[OS], or refuses it.")) -> template decl-wft-ec-evidence-publish (args/workflow_hitl_templates/ec-evidence-publish.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/evidence_collectors.py::EvidenceCollectorsTwin
- **Architecture: pass**
  - evidence: PM-EC-01 -> ec.collectors -> tests/test_icx_ent_02_evidence_collectors.py::test_every_collector_reads_its_source_and_every_observation_says_when_who_and_how_fresh, tests/test_icx_ent_02_evidence_collectors.py::test_a_source_that_says_no_is_false_and_one_that_cannot_say_is_unverified, tests/test_icx_ent_02_evidence_collectors.py::test_the_transport_only_reads_and_stays_on_its_host, tests/test_icx_ent_02_evidence_collectors.py::test_sigv4_matches_aws_published_get_vanilla_vector
  - evidence: PM-EC-02 -> ec.credentials -> tests/test_icx_ent_02_evidence_collectors.py::test_credentials_are_references_only_and_never_quoted, tests/test_icx_ent_02_evidence_collectors.py::test_the_ledger_is_append_only_hash_chained_on_the_shared_helper_and_holds_no_secret
  - evidence: PM-EC-03 -> ec.judge -> tests/test_icx_ent_02_evidence_collectors.py::test_freshness_parses_instants_and_never_passes_stale_future_or_timeless_evidence, tests/test_icx_ent_02_evidence_collectors.py::test_satisfied_needs_every_fact_and_a_fresh_false_is_a_finding_even_beside_an_unverified_one, tests/test_icx_ent_02_evidence_collectors.py::test_a_device_that_has_not_reported_leaves_its_fleet_unverified, tests/test_icx_ent_02_evidence_collectors.py::test_an_unreachable_source_or_an_unreadable_credential_is_unverified_with_why, tests/test_icx_ent_02_evidence_collectors.py::test_the_shipped_configuration_loads_and_every_mapping_row_is_bounded
  - evidence: PM-EC-04 -> ec.bundle -> tests/test_icx_ent_02_evidence_collectors.py::test_the_contract_is_icos_verbatim_and_pinned, tests/test_icx_ent_02_evidence_collectors.py::test_the_report_meets_icos_contract_and_icos_digest_rule, tests/test_icx_ent_02_evidence_collectors.py::test_one_person_prepares_and_another_of_the_org_publishes_the_embedded_report, tests/test_icx_ent_02_evidence_collectors.py::test_an_agent_only_prepares_and_a_changed_configuration_needs_a_new_report, tests/test_icx_ent_02_ambient.py::test_a_report_prepared_and_published_on_the_platform_database
  - evidence: PM-EC-05 -> ec.visibility -> tests/test_icx_ent_02_evidence_collectors.py::test_what_a_reader_may_not_see_is_counted_never_described, tests/test_icx_ent_02_evidence_collectors.py::test_the_reader_s_org_and_clearance_are_read_fresh_from_their_own_row, tests/test_icx_ent_02_evidence_collectors.py::test_a_tenant_person_s_canvas_grant_is_read_inside_their_own_request
  - evidence: PM-EC-06 -> if.ec.console, ec.twin, if.ec.mcp -> tests/test_icx_ent_02_evidence_collectors.py::test_the_routes_and_the_page, tests/test_icx_ent_02_evidence_collectors.py::test_the_twin_observes_with_threads_and_replays, tests/test_icx_ent_02_evidence_collectors.py::test_the_mcp_tools_read_and_only_prepare
- **AI-native: pass**
  - evidence: evidence_collectors_prepare mutating, approval tier 'unknown' (default_tier) halts
  - evidence: evidence_collectors_status read-only, tool_authorization tier 'read'

#### Signed factory builds: signed artefacts with SBOM/AI-BOM, provenance and VEX for ICDEV[OS] (`factory_builds.signed_builds`, component `factory_builds`)

- **GUI: pass**
  - evidence: factory_builds.fb_api_prepare called from tools/dashboard/templates/factory_builds/index.html
  - evidence: factory_builds.fb_api_publish called from tools/dashboard/templates/factory_builds/index.html
  - evidence: nav links /factory-builds
  - evidence: page /factory-builds/ renders factory_builds/index.html
  - evidence: registry entry 'factory_builds'
- **Workflow: pass**
  - evidence: factory_builds.fb_api_publish (declared (tools/factory_builds/blueprint.py: "A person, never the preparer, publishes a target's signed build to ICDEV[OS], or refuses it.")) -> template decl-wft-fb-build-publish (args/workflow_hitl_templates/fb-build-publish.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/factory_builds.py::FactoryBuildsTwin
- **Architecture: pass**
  - evidence: PM-FB-01 -> fb.key -> tests/test_icx_ent_03_factory_builds.py::test_no_key_is_refused_never_a_mac_and_a_test_key_lives_only_in_tmp, tests/test_icx_ent_03_factory_builds.py::test_a_keyring_key_and_a_kms_key_sign_and_every_signature_is_verified_first, tests/test_icx_ent_03_factory_builds.py::test_keyless_needs_a_configured_issuer_and_a_certificate_for_the_key_that_asked, tests/test_icx_ent_03_factory_builds.py::test_a_configured_rekor_log_receives_each_bundle_and_its_answer_is_recorded_not_trusted, tests/test_icx_ent_03_factory_builds.py::test_outbound_calls_are_https_or_loopback_only, tests/test_icx_ent_03_factory_builds.py::test_the_shipped_configuration_loads_with_no_key_and_the_log_off, tests/test_icx_ent_03_factory_builds.py::test_l1_a_test_key_is_pinned_to_the_real_temp_root_not_tmpdir, tests/test_icx_ent_03_factory_builds.py::test_the_e2e_key_is_honoured_only_under_tests_e2e_fixtures_and_only_with_no_configured_key, tests/test_icx_ent_03_factory_builds.py::test_l8_one_deadline_covers_every_rekor_submission_of_a_build
  - evidence: PM-FB-02 -> fb.build -> tests/test_icx_ent_03_factory_builds.py::test_icos_contracts_are_vendored_verbatim_and_pinned, tests/test_icx_ent_03_factory_builds.py::test_an_image_build_is_icos_shaped_signed_and_deterministic, tests/test_icx_ent_03_factory_builds.py::test_a_tampered_file_or_another_key_fails_the_self_check, tests/test_icx_ent_03_factory_builds.py::test_nothing_to_state_is_refused_never_an_invented_vex_and_a_scan_must_match_the_sbom, tests/test_icx_ent_03_factory_builds.py::test_inputs_are_read_only_under_the_root_never_through_a_symlink_or_a_hard_link, tests/test_icx_ent_03_factory_builds.py::test_m1_the_sbom_is_resolved_from_a_confined_copy_of_the_manifests, tests/test_icx_ent_03_factory_builds.py::test_l2_l3_a_fifo_or_a_symlinked_directory_on_the_way_is_refused_promptly
  - evidence: PM-FB-03 -> fb.aibom -> tests/test_icx_ent_03_factory_builds.py::test_a_model_build_carries_an_aibom_linked_to_its_registry_promotion_and_signed_weights, tests/test_icx_ent_03_factory_builds.py::test_only_a_promoted_version_is_built_and_an_override_is_said_to_be_one, tests/test_icx_ent_03_factory_builds.py::test_a_model_of_another_tenant_or_marked_above_the_target_is_refused, tests/test_icx_ent_03_factory_builds.py::test_a_symlinked_weights_file_is_never_followed, tests/test_icx_ent_03_factory_builds.py::test_a_model_demoted_since_it_was_built_is_never_published, tests/test_icx_ent_03_factory_builds.py::test_m2_a_registry_row_with_no_tenant_binds_only_to_a_platform_scoped_target, tests/test_icx_ent_03_factory_builds.py::test_l7_a_missing_dataset_is_refused_and_an_archived_version_no_longer_stands
  - evidence: PM-FB-04 -> fb.publish -> tests/test_icx_ent_03_factory_builds.py::test_a_person_prepares_another_of_the_org_publishes_and_only_then_it_leaves, tests/test_icx_ent_03_factory_builds.py::test_an_agent_never_makes_the_factory_key_sign, tests/test_icx_ent_03_factory_builds.py::test_a_publication_counts_only_with_its_declared_step, tests/test_icx_ent_03_factory_builds.py::test_what_a_reader_may_not_see_is_no_such_target_byte_for_byte, tests/test_icx_ent_03_factory_builds.py::test_the_ledger_is_append_only_and_hash_chained, tests/test_icx_ent_03_factory_builds.py::test_m4_only_a_builder_role_makes_the_factory_key_sign, tests/test_icx_ent_03_factory_builds.py::test_l5_a_build_is_published_only_under_the_factory_s_current_key, tests/test_icx_ent_03_factory_builds.py::test_l6_a_ledger_write_lost_after_the_step_publishes_nothing_and_says_so, tests/test_icx_ent_03_factory_builds.py::test_a_tenant_person_s_canvas_grant_is_read_inside_their_own_request, tests/test_icx_ent_03_ambient.py::test_builds_prepared_and_published_on_the_platform_database
  - evidence: PM-FB-05 -> if.fb.console, fb.twin, if.fb.mcp -> tests/test_icx_ent_03_factory_builds.py::test_the_routes_and_the_page, tests/test_icx_ent_03_factory_builds.py::test_the_twin_observes_with_threads_and_replays, tests/test_icx_ent_03_factory_builds.py::test_the_mcp_tool_only_reads, tests/test_icx_ent_03_factory_builds.py::test_l4_the_page_s_self_check_is_re_run_on_read, tests/test_icx_ent_03_factory_builds.py::test_l9_the_iqe_route_answers_over_its_own_collection_only
- **AI-native: pass**
  - evidence: factory_builds_status read-only, tool_authorization tier 'read'

#### Modernisation: AI-Ify opportunities, PRDs and their promotion (`mdz.aiify`, component `aiify`)

- **GUI: pass**
  - evidence: aiify.api_delete_all_scans called from tools/dashboard/templates/aiify/page.html
  - evidence: aiify.api_delete_scan called from tools/dashboard/templates/aiify/page.html
  - evidence: aiify.api_generate_prd called from tools/dashboard/templates/aiify/page.html
  - evidence: aiify.api_hitl_decision called from tools/dashboard/templates/aiify/page.html
  - evidence: aiify.api_iqe_query called from tools/dashboard/templates/aiify/page.html
  - evidence: aiify.api_posture_snapshot called from tools/dashboard/templates/aiify/posture.html
  - evidence: aiify.api_prd_dry_run called from tools/dashboard/templates/aiify/page.html
  - evidence: aiify.api_scan called from tools/dashboard/templates/aiify/page.html
  - evidence: aiify.api_send_to_kanban called from tools/dashboard/templates/aiify/page.html
  - evidence: nav links /ai-ify
  - evidence: nav links /ai-ify/posture
  - evidence: page /ai-ify/ renders aiify/page.html
  - evidence: page /ai-ify/posture renders aiify/posture.html
  - evidence: registry entry 'aiify'
- **Workflow: pass**
  - evidence: aiify.api_delete_all_scans (name carries 'delete') -> template decl-wft-mdz-aiify-scan-delete (args/workflow_hitl_templates/mdz-aiify-scan-delete.yaml)
  - evidence: aiify.api_delete_scan (name carries 'delete') -> template decl-wft-mdz-aiify-scan-delete (args/workflow_hitl_templates/mdz-aiify-scan-delete.yaml)
  - evidence: aiify.api_hitl_decision (name carries 'hitl') -> template decl-wft-mdz-aiify-promote (args/workflow_hitl_templates/mdz-aiify-promote.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/modernisation.py::ModernisationGovernanceTwin
- **Architecture: pass**
  - evidence: PM-MDZ-02 -> mdz.aiify, if.mdz.console -> tests/test_icx_mdz_01_modernisation.py::test_an_aiify_promotion_is_the_persons_step_before_anything_is_written, tests/test_icx_mdz_01_modernisation.py::test_an_aiify_promotion_that_cannot_be_recorded_changes_nothing, tests/test_icx_mdz_01_modernisation.py::test_send_to_kanban_still_fails_closed_when_decisions_cannot_be_read, tests/test_icx_mdz_01_modernisation.py::test_deleting_aiify_scans_is_a_persons_step
  - evidence: PM-MDZ-07 -> mdz.twin, if.mdz.mcp -> tests/test_icx_mdz_01_modernisation.py::test_the_twin_finds_each_gap_with_its_thread, tests/test_icx_mdz_01_modernisation.py::test_an_unreadable_source_is_unverified_never_a_pass, tests/test_icx_mdz_01_modernisation.py::test_the_twin_observes_records_and_replays, tests/test_icx_mdz_01_modernisation.py::test_the_modernisation_mcp_tools_are_registered_read_only, tests/test_icx_mdz_01_modernisation.py::test_the_pages_call_their_routes_by_endpoint_id
- **AI-native: pass**
  - evidence: aiify_promotions read-only, tool_authorization tier 'read'

#### Modernisation: cloud-native targets (container, config, twelve factors, events) (`mdz.cloud_native`, component `mdc`)

- **GUI: pass**
  - evidence: nav links /migration-canvas/modernisation
  - evidence: page /migration-canvas/modernisation renders migration_canvas/modernisation.html
  - evidence: registry entry 'mdc'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/modernisation.py::ModernisationGovernanceTwin
- **Architecture: pass**
  - evidence: PM-MDZ-05 -> mdz.cloud_native, if.mdz.console -> tests/test_icx_mdz_01_modernisation.py::test_cloud_native_reads_the_jobs_and_the_section_without_running_any
  - evidence: PM-MDZ-07 -> mdz.twin, if.mdz.mcp -> tests/test_icx_mdz_01_modernisation.py::test_the_twin_finds_each_gap_with_its_thread, tests/test_icx_mdz_01_modernisation.py::test_an_unreadable_source_is_unverified_never_a_pass, tests/test_icx_mdz_01_modernisation.py::test_the_twin_observes_records_and_replays, tests/test_icx_mdz_01_modernisation.py::test_the_modernisation_mcp_tools_are_registered_read_only, tests/test_icx_mdz_01_modernisation.py::test_the_pages_call_their_routes_by_endpoint_id
- **AI-native: pass**
  - evidence: modernisation_cloud_native read-only, tool_authorization tier 'read'

#### Modernisation: the ICDEV[OS] apptwin exporter and decision import (`mdz.exporter`, component `mdc`)

- **GUI: pass**
  - evidence: migration_canvas.api_modernisation_icos_check called from tools/dashboard/templates/migration_canvas/modernisation.html
  - evidence: migration_canvas.api_modernisation_ivv_signoff called from tools/dashboard/templates/migration_canvas/modernisation.html
  - evidence: migration_canvas.api_modernisation_process_review called from tools/dashboard/templates/migration_canvas/modernisation.html
  - evidence: nav links /migration-canvas/modernisation
  - evidence: page /migration-canvas/modernisation renders migration_canvas/modernisation.html
  - evidence: registry entry 'mdc'
- **Workflow: pass**
  - evidence: migration_canvas.api_modernisation_ivv_signoff (declared (tools/migration_canvas/modernisation.py: "An independent person signs off or refuses the application's survivor IV&V result")) -> template decl-wft-mdz-survivor-ivv-signoff (args/workflow_hitl_templates/mdz-survivor-ivv-signoff.yaml)
  - evidence: migration_canvas.api_modernisation_process_review (declared (tools/migration_canvas/modernisation.py: "A person confirms or rejects one extracted process")) -> template decl-wft-mdz-process-review (args/workflow_hitl_templates/mdz-process-review.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/modernisation.py::ModernisationGovernanceTwin
- **Architecture: pass**
  - evidence: PM-MDZ-06 -> mdz.exporter, if.mdz.console -> tests/test_icx_mdz_01_modernisation.py::test_the_modernisation_page_reads_apps_waves_and_the_bundle, tests/test_icx_mdz_01_modernisation.py::test_a_decisions_export_is_trusted_only_against_the_pinned_jwks
  - evidence: PM-MDZ-07 -> mdz.twin, if.mdz.mcp -> tests/test_icx_mdz_01_modernisation.py::test_the_twin_finds_each_gap_with_its_thread, tests/test_icx_mdz_01_modernisation.py::test_an_unreadable_source_is_unverified_never_a_pass, tests/test_icx_mdz_01_modernisation.py::test_the_twin_observes_records_and_replays, tests/test_icx_mdz_01_modernisation.py::test_the_modernisation_mcp_tools_are_registered_read_only, tests/test_icx_mdz_01_modernisation.py::test_the_pages_call_their_routes_by_endpoint_id
  - evidence: PM-MDZ-11 -> mdz.plan_gate, if.mdz.console -> tests/test_icx_mdz_02_seven_r_evidence.py::test_may_plan_refused_or_missing_writes_no_plan, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_planner_cli_and_the_mcp_tool_say_refused, tests/test_icx_mdz_02_seven_r_evidence.py::test_refactor_jobs_write_nothing_without_may_plan, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_7r_page_shows_confidence_unmeasured_and_the_refusal, tests/test_icx_modsamples_pipeline.py::test_the_first_run_plans_nothing_without_icdev_os_may_plan
  - evidence: PM-MDZ-13 -> mdz.bundle_features -> tests/test_icx_mdz_03_bundle_features.py::test_features_carry_file_line_evidence_and_capability_hints, tests/test_icx_mdz_03_bundle_features.py::test_an_observed_route_makes_a_feature_both_or_observed, tests/test_icx_mdz_03_bundle_features.py::test_the_same_source_gives_the_same_extract
  - evidence: PM-MDZ-14 -> mdz.bundle_features -> tests/test_icx_mdz_03_bundle_features.py::test_a_step_is_normalised_by_the_reviewed_rules_and_digested, tests/test_icx_mdz_03_bundle_features.py::test_a_rule_yaml_reads_as_a_boolean_is_refused, tests/test_icx_mdz_03_bundle_features.py::test_a_static_process_is_one_controllers_actions_in_source_order, tests/test_icx_mdz_03_bundle_features.py::test_an_observed_capture_is_a_process_with_its_signature, tests/test_icx_mdz_03_bundle_features.py::test_processify_from_source_needs_no_model
  - evidence: PM-MDZ-15 -> mdz.bundle_features, mdz.exporter -> tests/test_icx_mdz_03_bundle_features.py::test_the_bundle_carries_features_and_processes_and_still_validates, tests/test_icx_mdz_03_bundle_features.py::test_every_missing_part_is_said_with_its_reason, tests/test_icx_mdz_03_bundle_features.py::test_the_bundle_preview_counts_features_processes_and_what_is_missing, tests/test_icx_mdz_03_bundle_features.py::test_the_regenerated_real_bundles_validate_and_carry_features_and_processes, tests/test_icx_modsamples_pipeline.py::test_features_and_processes_come_without_a_model_and_every_part_is_said
  - evidence: PM-MDZ-16 -> mdz.bundle_features, mdz.twin, if.mdz.console, if.mdz.mcp -> tests/test_icx_mdz_03_bundle_features.py::test_reviewing_a_process_is_a_persons_step_bound_to_its_digest, tests/test_icx_mdz_03_bundle_features.py::test_the_page_shows_features_processes_and_calls_routes_by_id, tests/test_icx_mdz_03_bundle_features.py::test_the_twin_flags_no_features_unreviewed_processes_and_missing_parts, tests/test_icx_mdz_03_bundle_features.py::test_the_twin_observes_a_bundle_resolves_its_thread_and_replays, tests/test_icx_mdz_03_bundle_features.py::test_the_mcp_features_tool_is_read_only
  - evidence: PM-MDZ-17 -> mdz.bundle_contract, mdz.exporter, mdz.bundle_features -> tests/test_icx_mdz_04_bundle_fields.py::test_the_vendored_contract_is_icos_1_192_0_pinned_by_digest, tests/test_icx_mdz_04_bundle_fields.py::test_a_feature_seen_both_ways_is_both_with_its_hints_inline, tests/test_icx_mdz_04_bundle_fields.py::test_processes_carry_source_signature_review_and_step_fields, tests/test_icx_mdz_04_bundle_fields.py::test_a_reviewed_process_is_exported_reviewed_at_its_digest, tests/test_icx_mdz_04_bundle_fields.py::test_the_sections_map_is_inline_and_agrees_with_what_is_carried, tests/test_icx_mdz_04_bundle_fields.py::test_icos_agreement_rules_refuse_what_the_importer_would, tests/test_icx_mdz_04_bundle_fields.py::test_the_real_bundles_meet_the_pinned_contract_inline_with_no_side_file
  - evidence: PM-MDZ-18 -> mdz.bundle_contract, mdz.seven_r, mdz.plan_gate -> tests/test_icx_mdz_04_bundle_fields.py::test_the_7r_carries_confidence_unmeasured_and_assessable_as_fields, tests/test_icx_mdz_04_bundle_fields.py::test_waves_before_a_plan_are_derived_and_carry_no_estimate, tests/test_icx_mdz_04_bundle_fields.py::test_a_planned_wave_carries_the_sum_of_its_tasks_estimates_and_none_is_never_zero, tests/test_icx_retire_fitness.py::test_retire_reads_icos_measured_redundancy_only_from_the_signed_export, tests/test_icx_retire_fitness.py::test_the_assessment_reads_the_configured_export_and_records_what_it_read, tests/test_icx_retire_fitness.py::test_the_mcp_tool_passes_the_signed_redundancy_through, tests/test_icx_modsamples_pipeline.py::test_the_bundle_carries_the_1_189_fields_and_the_resumed_7r_reads_the_signed_redundancy, tests/test_icx_mdz_04_ambient.py::test_a_planned_waves_estimate_is_read_from_its_tasks
  - evidence: PM-MDZ-19 -> mdz.bundle_contract, mdz.twin, if.mdz.console, if.mdz.mcp -> tests/test_icx_mdz_04_bundle_fields.py::test_the_environment_comes_only_from_the_declared_file_with_its_source, tests/test_icx_mdz_04_bundle_fields.py::test_the_page_shows_confidence_unmeasured_security_domain_and_derived_waves, tests/test_icx_mdz_04_bundle_fields.py::test_the_bundle_preview_and_mcp_tool_carry_the_new_fields, tests/test_icx_mdz_04_bundle_fields.py::test_the_twin_flags_a_bundle_failing_the_contract_or_without_a_security_domain, tests/test_icx_mdz_04_bundle_fields.py::test_the_twin_observes_no_security_domain_with_a_thread_and_replays, tests/test_icx_mdz_04_ambient.py::test_the_bundle_preview_carries_the_7r_confidence_and_the_unreported_domain
  - evidence: PM-MDZ-20 -> mdz.wave_schedule, mdz.bundle_contract -> tests/test_icx_mdz_05_wave_dates.py::test_no_capacity_declared_dates_nothing_and_says_why, tests/test_icx_mdz_05_wave_dates.py::test_a_capacity_no_person_approved_at_its_digest_dates_nothing, tests/test_icx_mdz_05_wave_dates.py::test_a_reviewed_capacity_gives_exact_dates, tests/test_icx_mdz_05_wave_dates.py::test_dependency_order_is_respected_whatever_the_listing_order, tests/test_icx_mdz_05_wave_dates.py::test_a_wave_with_no_estimate_is_undated_and_so_is_every_wave_after_it, tests/test_icx_mdz_05_wave_dates.py::test_the_capacity_file_ships_empty_and_refuses_what_it_cannot_read, tests/test_icx_mdz_05_wave_dates.py::test_estimated_hours_is_the_sum_of_the_plans_own_task_estimates, tests/test_icx_mdz_05_wave_dates.py::test_before_a_plan_no_estimate_and_no_date_and_the_report_says_why, tests/test_icx_mdz_05_wave_dates.py::test_the_planner_dates_the_waves_only_once_the_capacity_is_approved_and_the_bundle_carries_them, tests/test_icx_mdz_05_wave_dates.py::test_without_a_capacity_the_bundle_says_why_and_still_validates, tests/test_icx_mdz_05_ambient.py::test_the_waves_are_dated_only_after_the_capacity_review_is_read_back
  - evidence: PM-MDZ-22 -> mdz.survivor_ivv, mdz.bundle_contract -> tests/test_icx_mdz_06_survivor_ivv.py::test_an_unverified_area_never_passes, tests/test_icx_mdz_06_survivor_ivv.py::test_the_run_is_ivv_assessor_over_the_outputs_and_names_everything, tests/test_icx_mdz_06_survivor_ivv.py::test_a_tampered_record_is_refused, tests/test_icx_mdz_06_survivor_ivv.py::test_with_no_outputs_every_area_is_unverified_and_nothing_is_certified, tests/test_icx_mdz_06_survivor_ivv.py::test_certify_only_when_every_area_passed_and_deny_on_a_critical_finding, tests/test_icx_mdz_06_survivor_ivv.py::test_the_cli_runs_and_shows, tests/test_icx_mdz_06_ambient.py::test_the_result_and_its_signoff_are_read_back_and_the_bundle_carries_it
  - evidence: PM-MDZ-23 -> mdz.survivor_ivv, mdz.twin, if.mdz.console, if.mdz.mcp -> tests/test_icx_mdz_06_survivor_ivv.py::test_signoff_by_the_plans_own_approver_is_refused, tests/test_icx_mdz_06_survivor_ivv.py::test_the_7r_record_cam_review_and_process_review_make_a_builder, tests/test_icx_mdz_06_survivor_ivv.py::test_the_signoff_is_a_declared_step_bound_to_the_digest_and_taken_once, tests/test_icx_mdz_06_survivor_ivv.py::test_the_template_is_declared_with_separation_from_builders, tests/test_icx_mdz_06_survivor_ivv.py::test_the_bundle_carries_only_a_signed_current_result_and_always_validates, tests/test_icx_mdz_06_survivor_ivv.py::test_a_newer_result_after_the_signoff_leaves_it_stale_and_uncarried, tests/test_icx_mdz_06_survivor_ivv.py::test_the_page_shows_the_result_per_area_and_offers_sign_and_refuse, tests/test_icx_mdz_06_survivor_ivv.py::test_the_twin_flags_unsigned_refused_and_unpassed_areas_with_a_thread, tests/test_icx_mdz_06_survivor_ivv.py::test_the_twin_observes_an_unsigned_result_with_a_thread_and_replays, tests/test_icx_mdz_06_survivor_ivv.py::test_the_mcp_survivor_ivv_tool_is_read_only_and_says_why
- **AI-native: pass**
  - evidence: modernisation_bundle read-only, tool_authorization tier 'read'
  - evidence: modernisation_features read-only, tool_authorization tier 'read'
  - evidence: modernisation_survivor_ivv read-only, tool_authorization tier 'read'

#### Modernisation: Process-Ify (document or observed process to workflow) (`mdz.processify`, component `wfc`)

- **GUI: pass**
  - evidence: nav links /workflow-canvas/processify
  - evidence: page /workflow-canvas/processify renders workflow_canvas/processify.html
  - evidence: registry entry 'wfc'
  - evidence: workflow_canvas.api_cleanup_processify_tasks called from tools/dashboard/templates/workflow_canvas/processify.html
  - evidence: workflow_canvas.api_observed_review called from tools/dashboard/templates/workflow_canvas/processify.html
  - evidence: workflow_canvas.api_processify_workflow called from tools/dashboard/templates/workflow_canvas/processify.html
  - evidence: workflow_canvas.api_save_processify_workflow called from tools/dashboard/templates/workflow_canvas/processify.html
- **Workflow: pass**
  - evidence: workflow_canvas.api_observed_review (declared (tools/workflow_canvas/blueprint.py: "HITL decision on an observed draft")) -> template decl-wft-mdz-processify-observed-approve (args/workflow_hitl_templates/mdz-processify-observed-approve.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/modernisation.py::ModernisationGovernanceTwin
- **Architecture: pass**
  - evidence: PM-MDZ-03 -> mdz.processify, if.mdz.console -> tests/test_icx_mdz_01_modernisation.py::test_reviewing_an_observed_draft_is_a_persons_step_and_names_them, tests/test_observed_processify.py::test_route_drafts_then_chain_link_waits_for_review
  - evidence: PM-MDZ-07 -> mdz.twin, if.mdz.mcp -> tests/test_icx_mdz_01_modernisation.py::test_the_twin_finds_each_gap_with_its_thread, tests/test_icx_mdz_01_modernisation.py::test_an_unreadable_source_is_unverified_never_a_pass, tests/test_icx_mdz_01_modernisation.py::test_the_twin_observes_records_and_replays, tests/test_icx_mdz_01_modernisation.py::test_the_modernisation_mcp_tools_are_registered_read_only, tests/test_icx_mdz_01_modernisation.py::test_the_pages_call_their_routes_by_endpoint_id
- **AI-native: pass**
  - evidence: processify_observed_drafts read-only, tool_authorization tier 'read'

#### Modernisation: 7R assessment and the strategy ICDEV proposes (`mdz.seven_r`, component `mdc`)

- **GUI: pass**
  - evidence: migration_api_legacy.record_strategy called from tools/dashboard/templates/migration.html
  - evidence: migration_api_legacy.trigger_assessment called from tools/dashboard/templates/migration.html
  - evidence: nav links /migration
  - evidence: page /migration renders migration.html
  - evidence: registry entry 'mdc'
- **Workflow: pass**
  - evidence: migration_api_legacy.record_strategy (declared (tools/dashboard/api/migration.py: "A person records the 7R strategy ICDEV proposes for an assessed application")) -> template decl-wft-mdz-7r-strategy (args/workflow_hitl_templates/mdz-7r-strategy.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/modernisation.py::ModernisationGovernanceTwin
- **Architecture: pass**
  - evidence: PM-MDZ-01 -> mdz.seven_r, if.mdz.console -> tests/test_icx_mdz_01_modernisation.py::test_the_assess_route_runs_the_assessor_for_one_application, tests/test_icx_mdz_01_modernisation.py::test_recording_a_7r_strategy_is_a_persons_step_and_only_a_proposal
  - evidence: PM-MDZ-07 -> mdz.twin, if.mdz.mcp -> tests/test_icx_mdz_01_modernisation.py::test_the_twin_finds_each_gap_with_its_thread, tests/test_icx_mdz_01_modernisation.py::test_an_unreadable_source_is_unverified_never_a_pass, tests/test_icx_mdz_01_modernisation.py::test_the_twin_observes_records_and_replays, tests/test_icx_mdz_01_modernisation.py::test_the_modernisation_mcp_tools_are_registered_read_only, tests/test_icx_mdz_01_modernisation.py::test_the_pages_call_their_routes_by_endpoint_id
  - evidence: PM-MDZ-08 -> mdz.seven_r -> tests/test_icx_mdz_02_seven_r_evidence.py::test_every_check_returns_exactly_its_catalogue_criteria_by_name, tests/test_icx_mdz_02_seven_r_evidence.py::test_a_renamed_criterion_is_refused_never_paired_by_position, tests/test_icx_mdz_02_seven_r_evidence.py::test_custom_weights_must_name_the_catalogues_criteria
  - evidence: PM-MDZ-09 -> mdz.seven_r -> tests/test_icx_mdz_02_seven_r_evidence.py::test_an_unmeasured_input_lowers_confidence_and_never_counts_half, tests/test_icx_mdz_02_seven_r_evidence.py::test_a_strategy_with_nothing_measured_is_not_assessable, tests/test_icx_mdz_02_seven_r_evidence.py::test_only_a_strategy_at_the_minimum_confidence_is_recommended, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_bundle_ranks_only_assessable_strategies, tests/test_icx_retire_fitness.py::test_unmeasured_is_left_out_never_half
  - evidence: PM-MDZ-10 -> mdz.seven_r, mdz.seven_r_consumers -> tests/test_icx_mdz_02_seven_r_evidence.py::test_the_three_generators_give_the_same_strategy_for_the_same_app, tests/test_icx_mdz_02_seven_r_evidence.py::test_no_assessment_or_no_evidence_means_no_generator_recommends, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_coa_chat_says_a_technology_alone_recommends_nothing
  - evidence: PM-MDZ-11 -> mdz.plan_gate, if.mdz.console -> tests/test_icx_mdz_02_seven_r_evidence.py::test_may_plan_refused_or_missing_writes_no_plan, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_planner_cli_and_the_mcp_tool_say_refused, tests/test_icx_mdz_02_seven_r_evidence.py::test_refactor_jobs_write_nothing_without_may_plan, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_7r_page_shows_confidence_unmeasured_and_the_refusal, tests/test_icx_modsamples_pipeline.py::test_the_first_run_plans_nothing_without_icdev_os_may_plan
  - evidence: PM-MDZ-12 -> mdz.twin, if.mdz.mcp -> tests/test_icx_mdz_02_seven_r_evidence.py::test_the_twin_finds_thin_evidence_and_plans_attempted_without_may_plan, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_twin_observes_a_refusal_resolves_its_thread_and_replays, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_mcp_read_tool_carries_confidence_unmeasured_and_refusals
  - evidence: PM-MDZ-18 -> mdz.bundle_contract, mdz.seven_r, mdz.plan_gate -> tests/test_icx_mdz_04_bundle_fields.py::test_the_7r_carries_confidence_unmeasured_and_assessable_as_fields, tests/test_icx_mdz_04_bundle_fields.py::test_waves_before_a_plan_are_derived_and_carry_no_estimate, tests/test_icx_mdz_04_bundle_fields.py::test_a_planned_wave_carries_the_sum_of_its_tasks_estimates_and_none_is_never_zero, tests/test_icx_retire_fitness.py::test_retire_reads_icos_measured_redundancy_only_from_the_signed_export, tests/test_icx_retire_fitness.py::test_the_assessment_reads_the_configured_export_and_records_what_it_read, tests/test_icx_retire_fitness.py::test_the_mcp_tool_passes_the_signed_redundancy_through, tests/test_icx_modsamples_pipeline.py::test_the_bundle_carries_the_1_189_fields_and_the_resumed_7r_reads_the_signed_redundancy, tests/test_icx_mdz_04_ambient.py::test_a_planned_waves_estimate_is_read_from_its_tasks
- **AI-native: pass**
  - evidence: assess_seven_r mutating, approval tier 'unknown' (default_tier) halts
  - evidence: modernisation_seven_r read-only, tool_authorization tier 'read'

#### Modernisation: an application's waves and CAM refactor jobs (`mdz.waves_cam`, component `mdc`)

- **GUI: pass**
  - evidence: migration_canvas.api_modernisation_capacity_review called from tools/dashboard/templates/migration_canvas/modernisation.html
  - evidence: migration_canvas.api_refactor_job_approve called from tools/dashboard/templates/migration_canvas/modernisation.html
  - evidence: nav links /migration-canvas/modernisation
  - evidence: page /migration-canvas/modernisation renders migration_canvas/modernisation.html
  - evidence: registry entry 'mdc'
- **Workflow: pass**
  - evidence: migration_canvas.api_modernisation_capacity_review (declared (tools/migration_canvas/modernisation.py: "A person approves or rejects the team capacity declared for one application")) -> template decl-wft-mdz-capacity-review (args/workflow_hitl_templates/mdz-capacity-review.yaml)
  - evidence: migration_canvas.api_refactor_job_approve (name carries 'approve') -> template decl-wft-mdz-cam-refactor-approve (args/workflow_hitl_templates/mdz-cam-refactor-approve.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/modernisation.py::ModernisationGovernanceTwin
- **Architecture: pass**
  - evidence: PM-MDZ-04 -> mdz.waves_cam, if.mdz.console -> tests/test_icx_mdz_01_modernisation.py::test_the_modernisation_page_reads_apps_waves_and_the_bundle, tests/test_icx_mdz_01_modernisation.py::test_approving_a_cam_jobs_output_binds_its_files_and_is_taken_once
  - evidence: PM-MDZ-07 -> mdz.twin, if.mdz.mcp -> tests/test_icx_mdz_01_modernisation.py::test_the_twin_finds_each_gap_with_its_thread, tests/test_icx_mdz_01_modernisation.py::test_an_unreadable_source_is_unverified_never_a_pass, tests/test_icx_mdz_01_modernisation.py::test_the_twin_observes_records_and_replays, tests/test_icx_mdz_01_modernisation.py::test_the_modernisation_mcp_tools_are_registered_read_only, tests/test_icx_mdz_01_modernisation.py::test_the_pages_call_their_routes_by_endpoint_id
  - evidence: PM-MDZ-11 -> mdz.plan_gate, if.mdz.console -> tests/test_icx_mdz_02_seven_r_evidence.py::test_may_plan_refused_or_missing_writes_no_plan, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_planner_cli_and_the_mcp_tool_say_refused, tests/test_icx_mdz_02_seven_r_evidence.py::test_refactor_jobs_write_nothing_without_may_plan, tests/test_icx_mdz_02_seven_r_evidence.py::test_the_7r_page_shows_confidence_unmeasured_and_the_refusal, tests/test_icx_modsamples_pipeline.py::test_the_first_run_plans_nothing_without_icdev_os_may_plan
  - evidence: PM-MDZ-20 -> mdz.wave_schedule, mdz.bundle_contract -> tests/test_icx_mdz_05_wave_dates.py::test_no_capacity_declared_dates_nothing_and_says_why, tests/test_icx_mdz_05_wave_dates.py::test_a_capacity_no_person_approved_at_its_digest_dates_nothing, tests/test_icx_mdz_05_wave_dates.py::test_a_reviewed_capacity_gives_exact_dates, tests/test_icx_mdz_05_wave_dates.py::test_dependency_order_is_respected_whatever_the_listing_order, tests/test_icx_mdz_05_wave_dates.py::test_a_wave_with_no_estimate_is_undated_and_so_is_every_wave_after_it, tests/test_icx_mdz_05_wave_dates.py::test_the_capacity_file_ships_empty_and_refuses_what_it_cannot_read, tests/test_icx_mdz_05_wave_dates.py::test_estimated_hours_is_the_sum_of_the_plans_own_task_estimates, tests/test_icx_mdz_05_wave_dates.py::test_before_a_plan_no_estimate_and_no_date_and_the_report_says_why, tests/test_icx_mdz_05_wave_dates.py::test_the_planner_dates_the_waves_only_once_the_capacity_is_approved_and_the_bundle_carries_them, tests/test_icx_mdz_05_wave_dates.py::test_without_a_capacity_the_bundle_says_why_and_still_validates, tests/test_icx_mdz_05_ambient.py::test_the_waves_are_dated_only_after_the_capacity_review_is_read_back
  - evidence: PM-MDZ-21 -> mdz.wave_schedule, mdz.twin, if.mdz.console, if.mdz.mcp -> tests/test_icx_mdz_05_wave_dates.py::test_reviewing_the_capacity_is_a_persons_step_bound_to_its_digest, tests/test_icx_mdz_05_wave_dates.py::test_the_page_shows_estimates_dates_why_and_the_capacity_review, tests/test_icx_mdz_05_wave_dates.py::test_the_twin_flags_an_unreviewed_capacity_and_undated_waves, tests/test_icx_mdz_05_wave_dates.py::test_the_twin_observes_undated_waves_with_a_thread_and_replays, tests/test_icx_mdz_05_wave_dates.py::test_the_mcp_wave_schedule_tool_is_read_only_and_says_why, tests/test_icx_mdz_05_wave_dates.py::test_the_e2e_capacity_file_is_honoured_only_under_tests
- **AI-native: pass**
  - evidence: modernisation_wave_schedule read-only, tool_authorization tier 'read'
  - evidence: modernisation_waves_jobs read-only, tool_authorization tier 'read'

#### Store publisher: signed agent-store packages for ICDEV[OS], prepared by one person and published by another (`store_publisher.signed_packages`, component `store_publisher`)

- **GUI: pass**
  - evidence: nav links /store-publisher
  - evidence: page /store-publisher/ renders store_publisher/index.html
  - evidence: registry entry 'store_publisher'
  - evidence: store_publisher.sp_api_prepare called from tools/dashboard/templates/store_publisher/index.html
  - evidence: store_publisher.sp_api_publish called from tools/dashboard/templates/store_publisher/index.html
- **Workflow: pass**
  - evidence: store_publisher.sp_api_publish (declared (tools/store_publisher/blueprint.py: "A person, never the preparer, publishes a prepared store package for ICDEV[OS], or refuses it.")) -> template icos-store-publish (args/workflow_hitl_templates/store-publish.yaml)
- **Twin: pass**
  - evidence: adapter tools/twin_core/adapters/store_publisher.py::StorePublisherTwin
- **Architecture: pass**
  - evidence: PM-SP-01 -> sp.card -> tests/test_icx_ent_04_store_publisher.py::test_no_key_is_refused_and_only_a_keyring_p384_key_signs, tests/test_icx_ent_04_store_publisher.py::test_the_card_is_jws_es384_over_jcs_and_verifies_only_under_a_pinned_key, tests/test_icx_ent_04_store_publisher.py::test_a_package_is_published_only_under_the_publisher_s_current_key, tests/test_icx_ent_04_store_publisher.py::test_publishing_checks_the_key_by_public_identity_and_says_when_it_cannot, tests/test_icx_ent_04_store_publisher.py::test_no_environment_variable_or_key_file_gives_the_product_a_key, tests/test_icx_ent_04_store_publisher.py::test_m4_one_rfc_8785_copy_serves_the_card_and_the_portfolio_decisions
  - evidence: PM-SP-02 -> sp.package -> tests/test_icx_ent_04_store_publisher.py::test_icos_store_contracts_are_vendored_verbatim_and_pinned, tests/test_icx_ent_04_store_publisher.py::test_a_package_meets_icos_contract_and_its_card_binds_its_content_and_tools, tests/test_icx_ent_04_store_publisher.py::test_every_kind_builds_a_package_that_meets_the_contract, tests/test_icx_ent_04_store_publisher.py::test_inputs_are_read_only_under_the_root_never_through_a_symlink_or_a_hard_link, tests/test_icx_ent_04_store_publisher.py::test_the_shipped_configuration_loads_with_no_key_and_fails_closed, tests/test_icx_ent_04_store_publisher.py::test_the_red_team_report_answers_every_id_and_no_time_is_in_the_future, tests/test_icx_ent_04_store_publisher.py::test_l1_the_red_team_report_is_read_once, tests/test_icx_ent_04_store_publisher.py::test_l8_a_symlinked_root_or_directory_is_never_written_or_read_through
  - evidence: PM-SP-03 -> sp.gates -> tests/test_icx_ent_04_store_publisher.py::test_the_seven_gates_run_on_the_package_and_a_gate_that_cannot_run_is_unverified, tests/test_icx_ent_04_store_publisher.py::test_the_scanner_sast_gate_never_passes_when_bandit_cannot_run, tests/test_icx_ent_04_store_publisher.py::test_m1_the_analysed_code_cannot_switch_the_sast_gate_off, tests/test_icx_ent_04_store_publisher.py::test_m1_a_python_file_bandit_did_not_analyse_is_unverified_and_named, tests/test_icx_ent_04_store_publisher.py::test_m2_a_source_that_is_not_python_is_never_analysed_so_never_passes
  - evidence: PM-SP-04 -> sp.publish, sp.retired -> tests/test_icx_ent_04_store_publisher.py::test_a_person_prepares_another_of_the_org_publishes_and_only_then_it_leaves, tests/test_icx_ent_04_store_publisher.py::test_an_agent_never_prepares_or_publishes, tests/test_icx_ent_04_store_publisher.py::test_only_a_packager_role_makes_the_publisher_key_sign, tests/test_icx_ent_04_store_publisher.py::test_a_publication_counts_only_with_its_declared_step, tests/test_icx_ent_04_store_publisher.py::test_what_a_reader_may_not_see_is_no_such_package_byte_for_byte, tests/test_icx_ent_04_store_publisher.py::test_the_ledger_is_append_only_and_hash_chained, tests/test_icx_ent_04_store_publisher.py::test_the_declared_publish_template_is_icos_s_step_and_separates_the_preparer, tests/test_icx_ent_04_ambient.py::test_packages_prepared_and_published_on_the_platform_database, tests/test_marketplace_publish.py::test_the_retired_marketplace_publish_and_review_refuse_and_point_to_the_packager, tests/test_icx_ent_04_store_publisher.py::test_m3_the_retired_pipeline_s_effect_is_not_reachable_another_way, tests/test_icx_ent_04_store_publisher.py::test_l4_only_a_person_who_packages_or_publishes_takes_the_bundle_out, tests/test_icx_ent_04_store_publisher.py::test_l5_the_signed_step_names_the_package_s_org, tests/test_icx_ent_04_store_publisher.py::test_l6_a_lost_ledger_write_voids_the_decided_step, tests/test_icx_ent_04_store_publisher.py::test_l7_no_fresh_person_row_is_no_reader
  - evidence: PM-SP-05 -> if.sp.console, sp.twin, if.sp.mcp -> tests/test_icx_ent_04_store_publisher.py::test_the_routes_and_the_page, tests/test_icx_ent_04_store_publisher.py::test_the_twin_observes_with_threads_and_replays, tests/test_icx_ent_04_store_publisher.py::test_the_mcp_tool_only_reads, tests/test_icx_ent_04_store_publisher.py::test_a_tenant_person_s_canvas_grant_is_read_inside_their_own_request
- **AI-native: pass**
  - evidence: store_publisher_status read-only, tool_authorization tier 'read'
<!-- END GENERATED feature-conformance -->

## Where the evidence differs from the plan's table

The plan (2026-11-14) was written from three read-only explorations. The audit corrects it here:

- **Cortex govern, MCP:** the plan said ✓. `cortex_govern` is declared mutating, so no read-only
  tool serves the feature (`ai:no-read-tool`).
- **Cortex resolve, complete, reason, classify, extract, GUI:** the plan said "partial (chat
  only)". No page calls any of the five routes, and no page renders for them (six GUI gaps).
- **Cortex agent launch, workflow:** two decisions, not one. The REST `agent` verb and the `/cortex`
  chat confirm card (`requires_confirm` in `api_chat`) both launch, and neither maps to a template.
  The MCP `cortex_agent_launch` already halts at the approval gate (default tier `unknown`).
- **Cortex metrics, GUI:** the plan said "mixed". `/cortex/metrics` renders and is one link from the
  nav (the Metrics button on `/cortex`), so it passes.
- **ACE launch, rerun, profile delete, GUI:** the plan said ✓. No page calls
  `DELETE /api/ace/profiles/<role_id>`, and no page calls `POST /api/ace/chat`.
- **ACE trust, sessions, trends, GUI:** `POST /api/ace/events/emit` is called by no page.
- **ACE abort, MCP:** the plan said ✓. Its only tool, `ace_abort`, mutates. The rule is excused with
  a stated reason: a stop has nothing of its own to read, and `ace_status` (under `ace.runs`) reads
  the run.
- **ACE launch MCP:** `ace_launch` is not enumerated in the approval policy, so it falls to the
  default tier `unknown`, which halts. It passes the AI-native and workflow rules on that evidence.
  The routes that launch from the console do not.
- **Twin and architecture:** as the plan said, no adapter and no platform model exist yet. Every
  feature has one gap under each.

## Remediation scope, ranked (icx-cnf-02..05)

Ranked by the number of open gaps each card closes, then by risk: a decision a person takes with no
declared workflow outranks a missing page.

### 1. icx-cnf-02: decisions as declared workflows (11 workflow gaps)

Declare templates under `args/workflow_hitl_templates/`, each `decides:` naming its surface and
holding a manual stage with a role; seed them into `wf_templates`; extend `check_hitl_workflow`.

1. `ace_api.api_hitl_resolve` — the mid-run HITL checkpoint (HITLGate today, no template).
2. `ace_api.api_suggestion_confirm` and `ace_api.api_suggestion_decline` — the suggested-team confirm.
3. `ace_api.api_launch` and `ace_api.api_session_rerun` — launching and rerunning a team.
4. `cortex.api_v1_agent` and `cortex.api_chat` (confirm card) — Cortex agent launch.
5. `cortex.api_v1_award` and `cortex.api_v1_bom` — award (separate from the proposer) and the BOM
   credibility designation.
6. `ace_api.api_profiles_delete` — deleting a role profile.
7. `cortex.findings.adjudicate` — needs a new endpoint and state first: the store says a person
   adjudicates, and nothing takes that decision.

Abort stays immediate (stated reason in the config).

### 2. icx-cnf-03: governance twin adapters (17 twin gaps)

Two adapters: `cortex` (canvas key `cortex`) and `ace` (canvas key `ace`), plus `cwk` for the
Co-Workers canvas and `twin_observatory` (or `observes: [conformance.audit]`) for this audit. Each
must carry `thread_id` on every violation and define `replay()`. One `cortex` adapter closes ten
rows at once; one `ace` adapter closes five. Cortex: TRUST-gate outcomes, `cortex_audit` chain health,
unadjudicated findings, backend liveness. ACE: runs, checkpoints waiting, aborts, trust drift.
Amend `docs/features/twx-cov-02-twin-coverage-audit.md` to record the reversal of its "no twin".

### 3. icx-cnf-04: Cortex and ACE in ICDEV's architecture model (17 architecture gaps)

Create `args/platform_model/` with one file per component: elements (type from the digital-thread
`VALID_TYPES`, linked to code), interfaces (REST `/cortex/api/v1`, MCP `cortex_*`, `/api/ace`,
`/coworker`, `/coworkers`) and requirements for every feature key, each traced to an existing test,
no orphans. Seed the digital thread from it, so the RTM gate holds these features.

### 4. icx-cnf-05: console and AI-native gaps (24 GUI gaps, 10 AI-native gaps)

Ranked within the card:

1. **PMO** (8 GUI, 1 AI): a page that calls the seven PMO verbs; read MCP tools, and award and BOM
   as proposals only.
2. **Governed verbs** (6 GUI): pages or panels for resolve, complete, reason, classify, extract.
3. **Intake** (3 GUI, 1 AI): an intake page that calls session create and turn; a read tool.
4. **Govern** (2 GUI, 1 AI): a TRUST-chain page; a read tool (a read-only govern check or a
   chain-status read).
5. **ACE** (3 GUI, 1 AI): call or retire `DELETE /api/ace/profiles/<role_id>`, `POST /api/ace/chat`
   and `POST /api/ace/events/emit` from a page; a read tool for HITL checkpoints and suggestions.
6. **Findings, metrics, Co-Workers, agent launch** (4 AI): `cortex_findings_list`,
   a Cortex metrics read, a Co-Workers roster read, a read for agent launches.
7. **Service keys and the air-gap guard** (2 GUI, 2 AI): a service-key page (list, grant, revoke —
   revoke and grant are decisions, so they go through icx-cnf-02's templates) and an air-gap posture
   panel, each with a read tool.

Capability-sheet entries follow each new tool.

## Notes

- Built in worktree `~/icdev-wt/icx-cnf-01` on `feat/icx-cnf-01-feature-conformance`, from
  `origin/main`. The probe runs the dashboard against a throwaway SQLite file
  (`ICDEV_DB_PATH` in a temp dir), never `data/icdev.db`.
- `args/workflow_hitl_templates/` and `args/platform_model/` do not exist yet. Their shape is the
  one this audit reads (see Method); icx-cnf-02 and icx-cnf-04 create them.
- Coherence check id `feature_conformance` is new in this card.

### icx-cnf-03: governance twins (2026-11-14)

- Four adapters close all 17 twin gaps: `cortex` (10 rows), `ace` (5),
  `cwk` (1) and `twin_observatory` observing `conformance.audit` (1). Open gaps
  79 → 62; `census_max` lowered to 62.
- Each carries `thread_id` on every violation and defines `replay()`, and
  each is a real twin, not a stub: `tools/twin_core/governance.py` records
  every observation's evidence in the append-only
  `twin_governance_observations` and replays it. What each observes, and the
  reversal of twx-cov-02's "no twin" decision for Cortex, ACE and Co-Workers, is
  in `docs/features/twx-cov-02-twin-coverage-audit.md` section 5.
- Console: `/twin-observatory/governance`. MCP: `twin_governance` (read-only).
- First reading against a scratch copy of a local `data/icdev.db` (window
  widened to cover its October rows): 52 of 57 governed Cortex calls had no
  provenance record, `cortex_audit` has no hash chain and no database trigger
  (append-only holds only at the agent hook), and the `rag` backend failed once.
  These are findings for a later card; this card only makes them visible.


### icx-cnf-06: Cortex provenance and cortex_audit integrity (2026-11-14)

- Root cause of the missing provenance: the provenance gate wrote through
  `tools/provenance/registry.register_citation`, whose default path was this
  checkout's `data/icdev.db`. On SQLite, `get_connection` opens a `.db` path
  that differs from `ICDEV_DB_PATH` as its own file, so the citation went to a
  different database from the audit row. That file had no
  `source_citation_registry` (only the blockchain migration created it), the
  INSERT failed, `register_citation` swallowed it into `""`, and the gate
  recorded `warn` with no reason saved. Every governed verb hit it: none skips
  the gate. The 5 calls the twin did not count had failed operations.
- Fix, in one place: the registry now uses the process database when no path
  is given; gate 7a writes on a connection to the audit row's database, ensures
  the registry table first, and lets a database error raise so the reason lands
  on the audit row (`gates_json.provenance_detail`). The audit row's id is the
  citation's `source_record_id`, so the two join both ways.
- `cortex_audit` is hash-chained (`chain_seq`, `prev_hash`, `row_hash`) and
  UPDATE/DELETE are refused by triggers on SQLite and PostgreSQL (migration
  `20261114175314`, `tools/cortex/db/audit_chain.py`). Old rows are never
  rewritten: one `cortex_audit_chain_genesis` row records their count and
  digest. `verify` catches an altered row, a removed row, an altered old row and
  a row written around the chain. The cortex twin runs it on every observation:
  `cortex.audit.chain_broken` (critical) threads to the first broken row.
- Twin readings on a scratch copy of the same database. Before: 52 untraced
  calls, `db_mutable`, `unchained`. After the migration and three new governed
  calls: provenance `pass` 3/3, chain intact (57 pre-chain rows covered by the
  genesis, 3 chained), no audit-chain finding. With the window widened to the
  October rows, `no_provenance` still counts the 52 old calls: they really have
  no record, and they leave the default 168-hour window on their own.
- PostgreSQL (never the live database): `bootstrap_pg.py` on a fresh database
  of the scratch server `icdev-scratch-pg` applies migration 20261114175314 with
  no failed migration; `tests/test_cortex_audit_integrity_pg.py` (in
  `tests/pg_tier_allowlist.txt`) passes there under `ICDEV_PYTEST_PG=1`:
  provenance lands and joins, rows read back from JSONB/TIMESTAMP/BOOLEAN hash to
  their `row_hash`, the PL/pgSQL triggers refuse UPDATE and DELETE (their RAISE
  has no `%`), and a row altered inside a rolled-back transaction is a break.
  `pg_pytest_tier.py --json`: ok.
- Not changed: a cache hit (`tools/cortex/cache.audit_hit`) still writes a
  chained audit row with no provenance id of its own; the twin does not count it
  (its outcome is `cache`, not `operation`). Gap counts unchanged (62).

### icx-cnf-07: the last two GUI gaps, and enforcement (2026-11-15)

- `gui:uncalled:ace_api.api_profiles_delete`: `/coworker/roles` lists the generated profiles (never a
  built-in role) with a Delete button. It asks the person to confirm, then calls
  `DELETE /api/ace/profiles/<role_id>`, whose route takes the person's step of
  `decl-wft-ace-profile-delete` in the request (held by admin, pm). The button is offered only to a
  holder; anyone else sees who holds it, and the route still refuses them. A refusal is shown.
- `gui:uncalled:cortex.api_v1_award`: `/cortex/pmo` has a Propose-award card that posts the
  opportunity id and nothing else. The route records a pending step of `decl-wft-cortex-award` and a
  draft contract; the card sends the person to `/decisions`, where a contracts person other than the
  proposer accepts or rejects it. Nothing on the page decides.
- Only the GUI cell moved for `ace.profiles` and `cortex.pmo`; workflow, twin, architecture and
  AI-native evidence is unchanged. 17/17 features conform, 0 open gaps.
- `enforcement` is now `fail` and `census_max` is 0, so a new gap fails `feature_conformance`.
  An audit that cannot be measured (the url_map probe fails) stays a warn that says so: it is
  unmeasured, not a gap and not a pass.

### icx-cnf-08: route ids, exact evidence, and e2e proof (2026-11-15)

The GUI rule used to decide "a page calls this route" by matching a route's literal pieces on one
line (`_path_regex`, `_refers`). That was wrong both ways: a page that built a path was credited
with every route of that shape, and a path assembled across lines was missed. Now:

- `tools/dashboard/routes_gen.py` generates `tools/dashboard/static/js/routes.gen.js` from the live
  url_map: every endpoint, its rules and methods, and the helpers `icdevRoute` and `icdevFetch`. They
  fill the rule, encode each value, and refuse a missing, empty or extra parameter, an unknown
  endpoint and a method the rule does not serve. base.html loads it in `<head>`, before any page
  script.
- Every template and script of the 17 features calls routes by endpoint id. In Jinja that is
  `url_for`; links to the features' pages use it too. The audit credits exactly the ids a file names
  in code. The regex matchers are gone.
- The audited files are derived, not listed: the templates the features' pages render, with their
  includes and scripts, and any file that names an audited endpoint. Any `/api/` in one of them
  fails `--check`, and with it the `feature_conformance` coherence check, as does a stale
  `routes.gen.js` or an id no route has. Pages outside the audit keep their paths until they join it.
- Runtime proof: `tests/e2e/fixtures/observed_calls.ts` records every request a page makes, matched
  to its route. Chromium's initiator stack tells a page's own request from a test's `page.evaluate`
  fetch: code a test evaluates has no script URL. CI's `E2E (Playwright)` job runs
  `feature_audit.py --e2e-gate` over all four shards. It fails on any write credited to a page that
  no page sent, on a committed record that claims more than the run saw, and on a request under an
  audited route that matches no route. `tests/e2e/feature_conformance_writes.spec.ts` presses every
  control. The committed record (`tests/e2e/observed-calls.json`) comes from a local run on a
  throwaway database: 35 of 35 credited writes were sent by a page.

What the exact evidence found:

1. **The suggested-team card had no working buttons on any page.** `confirm` and `decline` were
   credited from `includes/_chat_action_card.html`, but nothing rendered that card. `/chat` drew the
   stored card with the generic renderer, whose buttons do nothing, and `get_messages` did not even
   return the card's metadata. Fixed: `get_messages` returns `metadata`, and `chat.js` hands an
   action card to the shared renderer, refreshing as soon as the card is posted. The e2e confirms
   one team and declines another from their cards.
2. **"Turn auto on" could never be pressed.** The grant form shows only an auto-eligible role, and no
   shipped role is one (icx-ace-01). The e2e server adds one test-only role from
   `tests/e2e/fixtures/ace_roles` through `ICDEV_ACE_E2E_ROLES_DIR`. `RoleLoader` honours that only
   for a directory under `tests/`, never lets it override a shipped role, and no shipped role opts in.
   The e2e grants auto for it and turns it off again from the page.
3. **The audited canvases were off in CI's e2e.** No Cortex, document-intelligence or Twin
   Observatory page existed there, so none of their writes could ever be seen sent. The e2e job and
   `playwright.config.ts` now enable `ICDEV_CORTEX_ENABLED`, `ICDEV_DIC_ENABLED` and
   `ICDEV_TWIN_OBSERVATORY_ENABLED`.
4. **Pages reached only through literal hrefs** (`/coworker/{{ inst.id }}`, `/coworker/roles`,
   `/coworker/trust`, the sessions and chat pages, and the Twin Observatory's link to this page) now
   link by `url_for`, so "linked from a nav page" rests on an id.
5. **A credit from prose.** The Explorer named `cortex.api_v1_resolve` only in help text. It now
   links to the verbs page instead. The IQE query routes, credited from a
   `{% set iqe_api_route = "..." %}` string, are now `url_for(...)` and seen sent by the widget.

17/17 features conform, `enforcement: fail`, `census_max: 0`. The gap counts did not move: each
finding was closed in the code, and no reason was added.

### icx-mdz-01: modernisation joins the audit (2026-11-15)

Six modernisation features are now judged, under the same rules and the same gate: the 7R
assessment (`mdz.seven_r`), AI-Ify (`mdz.aiify`), Process-Ify (`mdz.processify`), an application's
waves and CAM refactor jobs (`mdz.waves_cam`), cloud-native targets (`mdz.cloud_native`), and the
ICDEV[OS] apptwin exporter and decision import (`mdz.exporter`). No blueprint is owned in full: the
migration API, the migration canvas and the workflow canvas also serve other features, so each
feature names its routes. Left out on purpose: `aiify.api_run_innovation` (the Innovation engine's
trigger) and Process-Ify's chain, regeneration and role routes (the workflow canvas's).

Added as first scoped, the six features had 40 open gaps (GUI 19, workflow 3, twin 6, architecture
6, AI-native 6), and the pages they render held 29 hand-written `/api/` paths. All are closed in
code. No reason, stop, machine endpoint or census line was added; `enforcement: fail` and
`census_max: 0` hold. 23 of 23 features conform.

- **GUI.** `/migration`, `/ai-ify`, `/ai-ify/posture` and `/workflow-canvas/processify` call every
  route by endpoint id. A new page, `/migration-canvas/modernisation`, shows an application's waves,
  its CAM refactor and cloud-native jobs (each file by SHA-256), the bundle the exporter would
  write, and a check of a signed ICDEV[OS] decisions export. The nav (Studio, Modernisation) links
  `/migration` and the new page. Fixed on the way: `POST /api/migration/assess` imported a module
  that does not exist and answered 501 for every call (it now runs `seven_r_assessor` for one
  application); the AI-Ify page keyed a PRD decision by phase only, so Send to Kanban never saw it
  (`<roadmap_id>:<phase_id>` is the key it reads); AI-Ify's scan deletion had no control on any page.
- **Workflow.** Five declared templates: `decl-wft-mdz-7r-strategy` (a person records the strategy
  ICDEV proposes, or declines to; the decided step is the record, marked not binding, because the
  signed decision is ICDEV[OS]'s and `icos_decisions.py may-plan` is the only answer to "may this be
  planned"), `decl-wft-mdz-aiify-promote` (the step is taken before `aiify_hitl_decisions` is
  written; Send to Kanban stays fail-closed), `decl-wft-mdz-aiify-scan-delete`,
  `decl-wft-mdz-processify-observed-approve` (the reviewer on the draft is the person who took the
  step, never a typed name) and `decl-wft-mdz-cam-refactor-approve` (bound to each file's SHA-256,
  once; nothing is applied).
- **Twin.** `tools/twin_core/adapters/modernisation.py`, canvas `mdc`, observes all six: an
  unrecorded strategy, a plan that diverges from (or has no) recorded strategy, an AI-Ify decision
  with no declared step, an observed draft waiting, a job's output unreviewed or missing on disk,
  the vendored bundle contract changed. Each finding has a thread `resolve_thread` follows.
- **Architecture.** `args/platform_model/modernisation.yaml`: PM-MDZ-01..07, requirement to element
  to code to test.
- **AI-native.** Six read-only MCP tools: `modernisation_seven_r`, `aiify_promotions`,
  `processify_observed_drafts`, `modernisation_waves_jobs`, `modernisation_cloud_native`,
  `modernisation_bundle`.
- **E2E.** `tests/e2e/modernisation_writes.spec.ts` presses every credited control over a synthetic
  application (`tests/e2e/fixtures/mdz01_app`, seeded by `modernisation_seed.py` through the
  analyser, CAM and Process-Ify). Process-Ify's digitize asks a model, and CI has none, so the test
  answers that one request with a fixed draft; the page still sends it, and the save and clean-up
  that follow are real. Local run with `ICDEV_NO_LLM=true` on a throwaway PostgreSQL: 52 of 52
  credited writes were sent by a page.
