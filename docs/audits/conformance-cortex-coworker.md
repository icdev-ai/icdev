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

17 features; 17 meet all five rules; 0 open gaps. Enforcement: `fail`.

### Matrix

| Feature | GUI | Workflow | Twin | Architecture | AI-native |
|---|---|---|---|---|---|
| ACE: abort (`ace.abort`) | pass | pass | pass | pass | excused |
| ACE: mid-run HITL checkpoint, suggested-team confirm (`ace.hitl`) | pass | pass | pass | pass | pass |
| ACE: role profiles (`ace.profiles`) | pass | pass | pass | pass | pass |
| ACE: launch, run, rerun (`ace.runs`) | pass | pass | pass | pass | pass |
| ACE: trust (NOVA), sessions, evaluations, trends, event feed and auto-dispatch (`ace.trust_sessions`) | pass | pass | pass | pass | pass |
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

### Gaps per rule

| Rule | Open gaps |
|---|---|
| GUI | 0 |
| Workflow | 0 |
| Twin | 0 |
| Architecture | 0 |
| AI-native | 0 |

### Routes: named by id, seen in e2e

Of 101 routes, 78 are named by a page by endpoint id (`icdevRoute`/`icdevFetch` in tools/dashboard/static/js/routes.gen.js, or `url_for`), and a page was seen sending 61 in the recorded e2e run (`tests/e2e/observed-calls.json`). Of the 35 writes a page names, 35 were seen: CI's e2e job fails on any it does not see (`--e2e-gate`). Reads may be credited by id alone.

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
| `ace.runs` | `POST /api/ace/launch` | `ace_api.api_launch` | tools/dashboard/templates/coworker/index.html | ✓ |
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
