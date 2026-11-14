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
| GUI | A GET route of the feature renders a template; one such page is in the nav or one link from a nav page; every mutating route is called by a page. | The live `url_map`, read in a scrubbed subprocess with the probe environment of `tools/dashboard/nav_paths.py` (every registry toggle on); view source; registry and `base.html` nav hrefs; every template and first-party script. |
| Workflow | Each decision maps to a template under `args/workflow_hitl_templates/` (its `decides:` names the surface) with a manual stage held by a role. An MCP decision may instead sit in an approval tier that halts. A stop stays immediate, with a stated reason. | Decision verbs in the endpoint or tool name (launch, rerun, abort, confirm, decline, approve, reject, delete, award, adjudicate, sign, waive, release, hitl); `HITLGate`, `approval_gate`, `requires_confirm`, `confirm_proposal` in the view source; decisions the config declares with a quotation that must exist in the named file; `args/agent_approval_policy.yaml`. |
| Twin | A `@register_twin` adapter in `tools/twin_core/adapters/` has the component as `canvas_key` (or names the feature in `observes`), carries `thread_id` on its violations and defines `replay()`. | The adapter source, parsed without importing it. |
| Architecture | Each requirement for the feature in `args/platform_model/*.yaml` names elements (or interfaces) of a digital-thread artifact type (`tools/mbse/digital_thread.py` `VALID_TYPES`), each linked to existing code (a file, or a `path::symbol` in it), and traces to an existing test (a pytest node whose file and function both exist); no element of the feature is an orphan. | The platform model files (the reviewed source the MBSE digital thread is seeded from), the files they name, parsed for the named symbols and test functions. |
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

The coherence check `feature_conformance` warns while gaps remain. It is in the full tier because
the url_map probe takes about 25 seconds; the fast tier runs it when a diff touches an audited
surface. Setting `enforcement: fail` flips it to fail once icx-cnf-02..05 land.

The audit follows its own rules: the console page `/twin-observatory/conformance` (linked from the
Twin Observatory), the JSON route `/twin-observatory/api/conformance`, and the read-only MCP tool
`feature_conformance`. It decides nothing, so it has no workflow. It is row `conformance.audit`
below, with its own twin and architecture gaps.

## Results

<!-- BEGIN GENERATED feature-conformance -->
_Generated by `python tools/conformance/feature_audit.py --markdown --census`. Do not edit between the markers._

17 features; 0 meet all five rules; 62 open gaps. Enforcement: `warn`.

### Matrix

| Feature | GUI | Workflow | Twin | Architecture | AI-native |
|---|---|---|---|---|---|
| ACE: abort (`ace.abort`) | pass | pass | gap (1) | pass | excused |
| ACE: mid-run HITL checkpoint, suggested-team confirm (`ace.hitl`) | pass | gap (3) | gap (1) | pass | gap (1) |
| ACE: role profiles (`ace.profiles`) | gap (1) | gap (1) | gap (1) | pass | pass |
| ACE: launch, run, rerun (`ace.runs`) | gap (1) | gap (2) | gap (1) | pass | pass |
| ACE: trust (NOVA), sessions, evaluations, trends (`ace.trust_sessions`) | gap (1) | n/a | gap (1) | pass | pass |
| Feature conformance audit (this tool) (`conformance.audit`) | pass | n/a | gap (1) | pass | pass |
| Cortex: agent launch (`cortex.agent_launch`) | pass | gap (2) | gap (1) | pass | gap (1) |
| Cortex: air-gap guard and health (`cortex.airgap_guard`) | gap (1) | n/a | gap (1) | pass | gap (1) |
| Cortex: resolution findings (`cortex.findings`) | pass | gap (1) | gap (1) | pass | gap (1) |
| Cortex: govern (TRUST chain) (`cortex.govern`) | gap (2) | n/a | gap (1) | pass | gap (1) |
| Cortex: resolve, complete, reason, classify, extract (`cortex.governed_verbs`) | gap (6) | n/a | gap (1) | pass | pass |
| Cortex: intake bridge (`cortex.intake`) | gap (3) | n/a | gap (1) | pass | gap (1) |
| Cortex: metrics (`cortex.metrics`) | pass | n/a | gap (1) | pass | gap (1) |
| Cortex: PMO verbs (slides, win themes, staffing, cost, dashboard, award, BOM) (`cortex.pmo`) | gap (8) | gap (2) | gap (1) | pass | gap (1) |
| Cortex: search and ask (`cortex.search_ask`) | pass | pass | gap (1) | pass | pass |
| Cortex: service keys (`cortex.service_keys`) | gap (1) | n/a | gap (1) | pass | gap (1) |
| Co-Workers canvas (`cwk.canvas`) | pass | n/a | gap (1) | pass | gap (1) |

### Gaps per rule

| Rule | Open gaps |
|---|---|
| GUI | 24 |
| Workflow | 11 |
| Twin | 17 |
| Architecture | 0 |
| AI-native | 10 |

### Evidence and gaps per feature

#### ACE: abort (`ace.abort`, component `ace`)

- **GUI: pass**
  - evidence: /coworker/<instance_id> linked from a nav page
  - evidence: ace_api.api_abort called from tools/dashboard/templates/coworker/instance.html
  - evidence: page /coworker/<instance_id> renders coworker/instance.html
  - evidence: registry entry 'ace'
- **Workflow: pass**
  - evidence: ace_api.api_abort: stop, immediate (stated reason)
  - evidence: mcp:ace_abort: stop, immediate (stated reason)
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes ace.abort (canvas_key 'ace' or observes)
- **Architecture: pass**
  - evidence: PM-ACE-07 -> ace.abort -> tests/test_ace_abort.py::test_abort_stops_every_thread_and_marks_the_run_cancelled, tests/test_ace_abort.py::test_abort_is_audited_with_its_actor, tests/test_ace_abort.py::test_abort_of_an_unknown_instance_raises_and_writes_nothing, tests/test_ace_abort.py::test_the_route_stops_the_run_in_one_request_with_no_approval, tests/test_ace_abort.py::test_the_route_returns_404_for_an_unknown_instance, tests/test_ace_abort.py::test_the_mcp_tool_aborts_and_records_mcp_as_the_actor
- **AI-native: excused**
  - excused: A stop has nothing of its own to read. An agent reads the run it would stop through ace_status, a read-only tool judged under ace.runs, and ace_abort itself halts at the approval gate (default tier unknown).
  - evidence: ace_abort mutating, approval tier 'unknown' (default_tier) halts

#### ACE: mid-run HITL checkpoint, suggested-team confirm (`ace.hitl`, component `ace`)

- **GUI: pass**
  - evidence: /coworker/<instance_id> linked from a nav page
  - evidence: ace_api.api_hitl_resolve called from tools/dashboard/templates/coworker/hitl.html
  - evidence: ace_api.api_suggestion_confirm called from tools/dashboard/templates/includes/_chat_action_card.html
  - evidence: ace_api.api_suggestion_decline called from tools/dashboard/templates/includes/_chat_action_card.html
  - evidence: page /coworker/<instance_id> renders coworker/instance.html
  - evidence: registry entry 'ace'
- **Workflow: gap**
  - gap `workflow:undeclared:ace_api.api_hitl_resolve`: ace_api.api_hitl_resolve is a decision (name carries 'hitl') with no declared workflow template
  - gap `workflow:undeclared:ace_api.api_suggestion_confirm`: ace_api.api_suggestion_confirm is a decision (name carries 'confirm') with no declared workflow template
  - gap `workflow:undeclared:ace_api.api_suggestion_decline`: ace_api.api_suggestion_decline is a decision (name carries 'decline') with no declared workflow template
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes ace.hitl (canvas_key 'ace' or observes)
- **Architecture: pass**
  - evidence: PM-ACE-05 -> ace.hitl, if.ace.api -> tests/test_ace_runtime.py::test_confidence_gate_fires_sets_pending_and_registers_event, tests/test_ace_runtime.py::test_hitl_resolve_wakes_thread_under_poll_interval, tests/test_ace_hitl_resolve_endpoint.py::test_hitl_resolve_endpoint_approves_confidence_gate, tests/test_ace_hitl_resolve_endpoint.py::test_hitl_resolve_endpoint_rejects_confidence_gate, tests/test_ace_hitl_resolve_endpoint.py::test_hitl_resolve_endpoint_requires_fields
  - evidence: PM-ACE-06 -> ace.hitl -> tests/test_ace_team_proposal.py::test_implicit_message_proposes_without_launching, tests/test_ace_team_proposal.py::test_declined_proposal_cannot_be_confirmed, tests/test_ace_team_proposal.py::test_confirm_is_idempotent, tests/test_ace_team_proposal.py::test_decisions_are_written_to_the_append_only_trail
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature

#### ACE: role profiles (`ace.profiles`, component `ace`)

- **GUI: gap**
  - gap `gui:uncalled:ace_api.api_profiles_delete`: mutating route /api/ace/profiles/<role_id> is called by no page
  - evidence: /coworker/roles linked from a nav page
  - evidence: ace_api.api_profiles_generate called from tools/dashboard/templates/coworker/profile_new.html
  - evidence: ace_api.api_profiles_preview called from tools/dashboard/templates/coworker/profile_new.html
  - evidence: ace_api.api_profiles_suggest_name called from tools/dashboard/templates/coworker/profile_new.html
  - evidence: page /coworker/profiles/new renders coworker/profile_new.html
  - evidence: page /coworker/roles renders coworker/roles.html
  - evidence: registry entry 'ace'
- **Workflow: gap**
  - gap `workflow:undeclared:ace_api.api_profiles_delete`: ace_api.api_profiles_delete is a decision (name carries 'delete') with no declared workflow template
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes ace.profiles (canvas_key 'ace' or observes)
- **Architecture: pass**
  - evidence: PM-ACE-08 -> ace.profiles.persona, if.ace.mcp -> tests/test_ace_persona_query.py::TestQueryPersona::test_uses_soul_identity_as_system_prompt, tests/test_ace_persona_query.py::TestQueryPersona::test_unknown_role_falls_back_to_generalist_system_prompt, tests/test_ace_persona_query.py::TestHandleAcePersonaQuery::test_exception_is_caught_and_returns_error_dict
  - evidence: PM-ACE-09 -> ace.profiles.persona -> tests/test_ace_persona_generator.py::test_get_or_generate_persona_requires_domain_description, tests/test_ace_persona_generator.py::test_get_or_generate_persona_reuses_cached_persona_without_a_new_llm_call
  - evidence: PM-ACE-10 -> ace.profiles.generator -> tests/test_ace_profile_generator.py::test_preview_writes_nothing_and_holds_the_model_to_the_allowlists, tests/test_ace_profile_generator.py::test_generate_writes_one_generated_role, tests/test_ace_profile_generator.py::test_generate_refuses_to_overwrite_a_builtin_role, tests/test_ace_profile_generator.py::test_a_caller_supplied_spec_is_held_to_the_same_allowlists, tests/test_ace_profile_generator.py::test_delete_removes_generated_roles_and_candidates_only, tests/test_ace_profile_generator.py::test_the_profile_routes_preview_generate_and_delete
- **AI-native: pass**
  - evidence: ace_ensure_sme mutating, approval tier 'unknown' (default_tier) halts
  - evidence: ace_persona_query read-only, tool_authorization tier 'read'

#### ACE: launch, run, rerun (`ace.runs`, component `ace`)

- **GUI: gap**
  - gap `gui:uncalled:ace_api.api_ace_chat`: mutating route /api/ace/chat is called by no page
  - evidence: /coworker/<instance_id> linked from a nav page
  - evidence: ace_api.api_launch called from tools/dashboard/templates/coworker/index.html
  - evidence: ace_api.api_session_rerun called from tools/dashboard/templates/ace/session_detail.html
  - evidence: nav links /coworker
  - evidence: page /coworker renders coworker/index.html
  - evidence: page /coworker/ renders coworker/index.html
  - evidence: page /coworker/<instance_id> renders coworker/instance.html
  - evidence: page /coworker/live/<instance_id> renders ace/live.html
  - evidence: registry entry 'ace'
- **Workflow: gap**
  - gap `workflow:undeclared:ace_api.api_launch`: ace_api.api_launch is a decision (name carries 'launch') with no declared workflow template
  - gap `workflow:undeclared:ace_api.api_session_rerun`: ace_api.api_session_rerun is a decision (name carries 'rerun') with no declared workflow template
  - evidence: mcp:ace_launch (name carries 'launch') -> approval tier 'unknown' (default_tier) halts in args/agent_approval_policy.yaml
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes ace.runs (canvas_key 'ace' or observes)
- **Architecture: pass**
  - evidence: PM-ACE-01 -> ace.runs, if.ace.api -> tests/test_ace_runtime.py::test_controller_launch_returns_instance_id, tests/test_ace_runtime.py::test_controller_launch_threads_role_ids_to_run
  - evidence: PM-ACE-02 -> ace.runs, if.ace.console -> tests/test_ace_session_persistence.py::test_select_by_resume_token_returns_one_row, tests/test_ace_session_persistence.py::test_append_turn_and_reload, tests/test_ace_session_persistence.py::test_duplicate_resume_token_raises, tests/test_ace_instance_page_render.py::test_instance_page_with_session_exposes_resume_token
  - evidence: PM-ACE-03 -> ace.runs, if.ace.console, if.ace.api -> tests/test_ace_instance_page_render.py::test_template_failure_on_page_route_is_500_html_not_200_json, tests/test_ace_instance_page_render.py::test_unknown_instance_is_404, tests/test_ace_instance_page_render.py::test_audit_endpoint_returns_events_array_and_count
  - evidence: PM-ACE-04 -> ace.runs -> tests/test_ace_runtime.py::test_coworker_thread_step_sequence, tests/test_ace_runtime.py::test_coworker_thread_trust_denied
- **AI-native: pass**
  - evidence: ace_launch mutating, approval tier 'unknown' (default_tier) halts
  - evidence: ace_status read-only, tool_authorization tier 'read'

#### ACE: trust (NOVA), sessions, evaluations, trends (`ace.trust_sessions`, component `ace`)

- **GUI: gap**
  - gap `gui:uncalled:ace_api.api_ace_events_emit`: mutating route /api/ace/events/emit is called by no page
  - evidence: /coworker/sessions/<session_id> linked from a nav page
  - evidence: /coworker/trust linked from a nav page
  - evidence: ace_api.api_iqe_query called from tools/dashboard/templates/coworker/index.html
  - evidence: ace_api.api_session_eval_post called from tools/dashboard/templates/ace/evals.html
  - evidence: nav links /coworker/sessions
  - evidence: page /coworker/evals renders ace/evals.html
  - evidence: page /coworker/evals/trends renders ace/trends.html
  - evidence: page /coworker/sessions renders ace/sessions.html
  - evidence: page /coworker/sessions/<session_id> renders ace/session_detail.html
  - evidence: page /coworker/trust renders ace/trust.html
  - evidence: registry entry 'ace'
- **Workflow: n/a**
  - evidence: 3 mutating route(s), none a decision a person takes
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes ace.trust_sessions (canvas_key 'ace' or observes)
- **Architecture: pass**
  - evidence: PM-ACE-11 -> ace.trust, if.ace.api -> tests/test_ace_nova_state_endpoint.py::test_nova_state_trust_score_present, tests/test_ace_nova_state_endpoint.py::test_nova_state_default_trust_when_no_ledger, tests/test_ace_nova_state_endpoint.py::test_nova_state_legacy_improvement_reports_no_evidence_honestly
  - evidence: PM-ACE-12 -> ace.trust -> tests/test_ace_event_bus.py::TestPublishSubscribe::test_different_instances_isolated, tests/test_ace_event_bus.py::TestPublishSubscribe::test_full_queue_drops_event, tests/test_ace_event_bus.py::TestThreadSafety::test_concurrent_publish_and_subscribe
  - evidence: PM-ACE-13 -> ace.evaluations -> tests/test_ace_eval_routes.py::test_post_scores_a_stored_session_and_persists_it, tests/test_ace_eval_routes.py::test_an_unknown_session_is_a_404_and_writes_nothing, tests/test_ace_eval_routes.py::test_the_read_side_returns_what_was_stored, tests/test_agent_evaluator.py::TestScoreFromDB::test_score_existing_session, tests/test_agent_evaluator.py::TestSaveAndGetEval::test_save_and_retrieve, tests/test_agent_evaluator.py::TestEvalRunner::test_load_suite_parses_entries, tests/test_agent_evaluator.py::TestEvalRunner::test_run_eval_router_failure, tests/test_eval_trends.py::TestGetEvalTrends::test_fallback_query_used_when_join_fails
- **AI-native: pass**
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
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes conformance.audit (canvas_key 'twin_observatory' or observes)
- **Architecture: pass**
  - evidence: PM-CNF-01 -> conformance.audit -> tests/test_feature_conformance_audit.py::test_the_baseline_meets_all_five_rules, tests/test_feature_conformance_audit.py::test_architecture_an_orphan_element_and_a_missing_test_are_gaps, tests/test_feature_conformance_audit.py::test_a_stated_reason_excuses_only_its_own_rule, tests/test_feature_conformance_audit.py::test_a_reason_that_excuses_nothing_is_stale
  - evidence: PM-CNF-02 -> conformance.audit -> tests/test_feature_conformance_audit.py::test_two_runs_and_a_shuffled_url_map_are_byte_identical, tests/test_feature_conformance_audit.py::test_check_fails_on_a_stale_doc_snapshot_or_census, tests/test_feature_conformance_audit.py::test_the_census_ceiling_may_not_be_exceeded
  - evidence: PM-CNF-03 -> conformance.audit, if.conformance.console, if.conformance.mcp -> tests/test_feature_conformance_audit.py::test_the_console_page_and_api_render_the_snapshot, tests/test_feature_conformance_audit.py::test_the_mcp_tool_is_declared_read_only_and_serves_the_snapshot
  - evidence: PM-CNF-04 -> platform_model.seeder -> tests/test_platform_model.py::test_the_shipped_model_validates, tests/test_platform_model.py::test_an_invented_test_name_is_refused, tests/test_platform_model.py::test_an_untested_requirement_is_a_gap_not_a_pass, tests/test_platform_model.py::test_seeded_thread_has_no_orphans_or_gaps_for_traced_features, tests/test_platform_model.py::test_mcp_trace_forward_and_backward_resolve_a_requirement
  - evidence: PM-CNF-05 -> if.lineage.platform_model, if.conformance.mcp -> tests/test_platform_model.py::test_lineage_route_and_page_show_the_trace, tests/test_platform_model.py::test_mcp_platform_model_trace_is_read_only
- **AI-native: pass**
  - evidence: feature_conformance read-only, tool_authorization tier 'read'

#### Cortex: agent launch (`cortex.agent_launch`, component `cortex`)

- **GUI: pass**
  - evidence: cortex.api_chat called from tools/dashboard/templates/cortex/index.html
  - evidence: cortex.api_v1_agent machine surface (stated reason)
  - evidence: nav links /cortex
  - evidence: page /cortex/ renders cortex/index.html
  - evidence: registry entry 'cortex'
- **Workflow: gap**
  - gap `workflow:undeclared:cortex.api_chat`: cortex.api_chat is a decision (declared (tools/cortex/blueprint.py: "confirm to proceed")) with no declared workflow template
  - gap `workflow:undeclared:cortex.api_v1_agent`: cortex.api_v1_agent is a decision (declared (tools/cortex/rest_v1.py: "it is the one endpoint here that")) with no declared workflow template
  - evidence: mcp:cortex_agent_launch (name carries 'launch') -> approval tier 'unknown' (default_tier) halts in args/agent_approval_policy.yaml
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.agent_launch (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-10 -> cortex.agent_launch, if.cortex.rest_v1 -> tests/cortex/test_rest_agent.py::test_agent_is_not_in_the_default_grant, tests/cortex/test_rest_agent.py::test_scope_missing_403, tests/cortex/test_rest_agent.py::test_identity_comes_from_the_session_not_the_body, tests/cortex/test_rest_agent.py::test_tools_and_handlers_are_never_forwarded
  - evidence: PM-CTX-11 -> cortex.agent_launch -> tests/cortex/test_chat_routing.py::TestChatRouting::test_agent_intent_gated_behind_confirm, tests/cortex/test_agent_confirm.py::test_unconfirmed_agent_does_not_launch, tests/cortex/test_agent_confirm.py::test_confirmed_agent_launches_via_governed_facade, tests/cortex/test_agent_confirm.py::test_proposal_names_the_actual_roster
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature
  - evidence: cortex_agent_launch mutating, approval tier 'unknown' (default_tier) halts

#### Cortex: air-gap guard and health (`cortex.airgap_guard`, component `cortex`)

- **GUI: gap**
  - gap `gui:no-page`: no GET route of this feature renders a template
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.airgap_guard (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-22 -> cortex.airgap_guard -> tests/cortex/test_summarize_routing_airgap.py::test_the_airgap_guard_can_see_this_function, tests/cortex/test_summarize_routing_airgap.py::test_airgap_context_threads_exclude_model_ids, tests/cortex/test_summarize_routing_airgap.py::test_every_declared_cortex_routing_function_exists_under_routing
  - evidence: PM-CTX-23 -> cortex.airgap_guard, if.cortex.rest_v1 -> tests/cortex/test_rest_scopes.py::test_health_requires_no_auth
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature

#### Cortex: resolution findings (`cortex.findings`, component `cortex`)

- **GUI: pass**
  - evidence: nav links /document-intelligence/explorer
  - evidence: page /document-intelligence/explorer renders document_intelligence/explorer.html
  - evidence: registry entry 'cortex'
- **Workflow: gap**
  - gap `workflow:no-endpoint:cortex.findings.adjudicate`: a person decides this (tools/cortex/finding_store.py: "A conflict is a finding a HUMAN adjudicates") but no endpoint takes the decision
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.findings (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-12 -> cortex.findings -> tests/cortex/test_finding_store.py::TestNoSilentWinner::test_no_winner_column_exists, tests/cortex/test_finding_store.py::TestNoSilentWinner::test_both_values_are_kept, tests/cortex/test_finding_store.py::TestConflictRoundTrip::test_each_side_keeps_its_own_source_and_as_of
  - evidence: PM-CTX-13 -> cortex.findings -> tests/cortex/test_explorer_conflict_render.py::TestNoSilentResolution::test_the_page_says_it_is_unresolved_by_design, tests/cortex/test_explorer_conflict_render.py::TestNoSilentResolution::test_neither_side_is_hidden, tests/cortex/test_finding_store.py::TestGapBrowsing::test_all_gaps_are_browsable
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature

#### Cortex: govern (TRUST chain) (`cortex.govern`, component `cortex`)

- **GUI: gap**
  - gap `gui:no-page`: no GET route of this feature renders a template
  - gap `gui:uncalled:cortex.api_v1_govern`: mutating route /cortex/api/v1/govern is called by no page
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 1 mutating route(s), none a decision a person takes
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.govern (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-08 -> cortex.govern, if.cortex.rest_v1 -> tests/cortex/test_rest_api.py::test_govern_returns_report, tests/cortex/test_rest_api.py::test_governance_block_returns_403, tests/cortex/test_rest_single_governance.py::test_the_govern_endpoint_still_runs_the_chain_once
  - evidence: PM-CTX-09 -> cortex.govern -> tests/cortex/test_governance_kg_gate.py, tests/cortex/test_governance_timing.py
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature
  - evidence: cortex_govern mutating, approval tier 'unknown' (default_tier) halts

#### Cortex: resolve, complete, reason, classify, extract (`cortex.governed_verbs`, component `cortex`)

- **GUI: gap**
  - gap `gui:no-page`: no GET route of this feature renders a template
  - gap `gui:uncalled:cortex.api_v1_classify`: mutating route /cortex/api/v1/classify is called by no page
  - gap `gui:uncalled:cortex.api_v1_complete`: mutating route /cortex/api/v1/complete is called by no page
  - gap `gui:uncalled:cortex.api_v1_extract`: mutating route /cortex/api/v1/extract is called by no page
  - gap `gui:uncalled:cortex.api_v1_reason`: mutating route /cortex/api/v1/reason is called by no page
  - gap `gui:uncalled:cortex.api_v1_resolve`: mutating route /cortex/api/v1/resolve is called by no page
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 5 mutating route(s), none a decision a person takes
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.governed_verbs (canvas_key 'cortex' or observes)
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

- **GUI: gap**
  - gap `gui:no-page`: no GET route of this feature renders a template
  - gap `gui:uncalled:cortex.api_v1_intake_session_create`: mutating route /cortex/api/v1/intake/session is called by no page
  - gap `gui:uncalled:cortex.api_v1_intake_turn`: mutating route /cortex/api/v1/intake/turn is called by no page
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 2 mutating route(s), none a decision a person takes
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.intake (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-18 -> cortex.intake, if.cortex.rest_v1 -> tests/cortex/test_rest_intake.py::test_create_seeds_verbatim_ask_as_first_turn, tests/cortex/test_rest_intake.py::test_turn_other_tenant_session_404, tests/cortex/test_rest_intake.py::test_get_session_whitelists_fields, tests/cortex/test_rest_intake.py::test_scope_missing_403
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature

#### Cortex: metrics (`cortex.metrics`, component `cortex`)

- **GUI: pass**
  - evidence: /cortex/metrics linked from a nav page
  - evidence: page /cortex/metrics renders cortex/metrics.html
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.metrics (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-21 -> cortex.metrics -> tests/cortex/test_metrics.py::test_missing_table_reports_unavailable, tests/cortex/test_metrics.py::test_healthy_but_empty_window_reports_idle, tests/cortex/test_metrics.py::test_counts_stay_exact_when_detail_is_truncated, tests/cortex/test_metrics.py::test_metrics_page_discloses_a_truncated_detail_read
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature

#### Cortex: PMO verbs (slides, win themes, staffing, cost, dashboard, award, BOM) (`cortex.pmo`, component `cortex`)

- **GUI: gap**
  - gap `gui:no-page`: no GET route of this feature renders a template
  - gap `gui:uncalled:cortex.api_v1_award`: mutating route /cortex/api/v1/award is called by no page
  - gap `gui:uncalled:cortex.api_v1_bom`: mutating route /cortex/api/v1/bom is called by no page
  - gap `gui:uncalled:cortex.api_v1_cost_volume`: mutating route /cortex/api/v1/cost_volume is called by no page
  - gap `gui:uncalled:cortex.api_v1_dashboard`: mutating route /cortex/api/v1/dashboard is called by no page
  - gap `gui:uncalled:cortex.api_v1_slides`: mutating route /cortex/api/v1/slides is called by no page
  - gap `gui:uncalled:cortex.api_v1_staffing_matrix`: mutating route /cortex/api/v1/staffing_matrix is called by no page
  - gap `gui:uncalled:cortex.api_v1_win_themes`: mutating route /cortex/api/v1/win_themes is called by no page
  - evidence: registry entry 'cortex'
- **Workflow: gap**
  - gap `workflow:undeclared:cortex.api_v1_award`: cortex.api_v1_award is a decision (name carries 'award') with no declared workflow template
  - gap `workflow:undeclared:cortex.api_v1_bom`: cortex.api_v1_bom is a decision (declared (tools/cortex/rest_v1.py: "Only a human's designation binds")) with no declared workflow template
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.pmo (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-14 -> cortex.pmo.award, if.cortex.rest_v1 -> tests/cortex/test_rest_award.py::test_award_scope_exists_but_is_not_granted_by_default, tests/cortex/test_rest_award.py::test_pricing_a_bid_does_not_let_you_declare_it_won, tests/cortex/test_rest_award.py::test_award_requires_authentication
  - evidence: PM-CTX-15 -> cortex.pmo.evidence -> tests/cortex/test_rest_win_themes.py::test_uncited_theme_is_refused_not_stored, tests/cortex/test_rest_staffing_matrix.py::test_an_unevidenced_person_is_REFUSED_not_stored, tests/cortex/test_rest_cost_volume.py::test_an_unrated_lcat_refuses_the_price_rather_than_guessing
  - evidence: PM-CTX-16 -> cortex.pmo.render -> tests/cortex/test_rest_slides.py::test_image_path_never_reaches_the_builder, tests/cortex/test_rest_dashboard.py::test_a_path_bearing_key_NEVER_reaches_the_renderer, tests/cortex/test_rest_dashboard.py::test_the_caller_cannot_mark_its_own_export_DOWN
  - evidence: PM-CTX-17 -> cortex.pmo.bom -> tests/cortex/test_rest_bom.py::test_the_route_needs_a_caller_and_the_bom_scope, tests/cortex/test_rest_bom.py::test_an_undeclared_tier_is_proposed_never_self_approved, tests/cortex/test_rest_bom.py::test_only_the_callers_declaration_binds, tests/cortex/test_rest_bom.py::test_no_documents_is_a_400_naming_the_defect, tests/bom/test_cortex_bom.py::TestTheScope::test_it_is_never_in_the_default_grant, tests/bom/test_cortex_bom.py::TestSilenceIsNeverConfirmation::test_what_the_caller_omits_is_PROPOSED_and_says_so, tests/bom/test_cortex_bom.py::TestTheSecurityPosture::test_nothing_the_caller_uploads_is_persisted
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature

#### Cortex: search and ask (`cortex.search_ask`, component `cortex`)

- **GUI: pass**
  - evidence: cortex.api_chat called from tools/dashboard/templates/cortex/index.html
  - evidence: cortex.api_iqe_query called from tools/dashboard/templates/cortex/index.html
  - evidence: cortex.api_v1_ask machine surface (stated reason)
  - evidence: cortex.api_v1_search machine surface (stated reason)
  - evidence: nav links /cortex
  - evidence: page /cortex/ renders cortex/index.html
  - evidence: registry entry 'cortex'
- **Workflow: pass**
  - evidence: cortex.api_chat: not a decision (stated reason)
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.search_ask (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-01 -> cortex.search_ask, if.cortex.rest_v1 -> tests/cortex/test_rest_api.py::test_search_returns_normalized_rows, tests/cortex/test_rest_api.py::test_ask_returns_cortex_result, tests/cortex/test_rest_api.py::test_ask_query_blocked_returns_403, tests/cortex/test_rest_api.py::test_ask_unanswerable_returns_422
  - evidence: PM-CTX-02 -> cortex.search_ask, if.cortex.console -> tests/cortex/test_chat_routing.py::TestChatRouting::test_search_message_calls_cortex_search, tests/cortex/test_chat_routing.py::TestChatRouting::test_data_question_calls_cortex_ask, tests/cortex/test_chat_routing.py::TestChatRouting::test_explicit_mode_overrides_routing, tests/cortex/test_chat_routing.py::TestChatRouting::test_facade_error_degrades_not_500, tests/cortex/test_blueprint_routes.py::TestPageRoutes::test_index_returns_200_and_branding
  - evidence: PM-CTX-03 -> cortex.search_ask, if.cortex.mcp -> tests/cortex/test_mcp_tools.py::test_registry_lists_all_cortex_tools, tests/cortex/test_mcp_tools.py::test_search_round_trips_results, tests/cortex/test_mcp_tools.py::test_search_requires_query, tests/cortex/test_mcp_tools.py::test_search_wraps_backend_error
- **AI-native: pass**
  - evidence: cortex_ask read-only, tool_authorization tier 'read'
  - evidence: cortex_search read-only, tool_authorization tier 'read'

#### Cortex: service keys (`cortex.service_keys`, component `cortex`)

- **GUI: gap**
  - gap `gui:no-page`: no GET route of this feature renders a template
  - evidence: registry entry 'cortex'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cortex.service_keys (canvas_key 'cortex' or observes)
- **Architecture: pass**
  - evidence: PM-CTX-19 -> cortex.service_keys -> tests/cortex/test_service_keys.py::test_raw_key_never_stored, tests/cortex/test_service_keys.py::test_revoked_key_rejected, tests/cortex/test_service_keys.py::test_tenant_never_from_request, tests/cortex/test_service_keys.py::test_classification_clamped_to_ceiling
  - evidence: PM-CTX-20 -> cortex.service_keys, if.cortex.rest_v1 -> tests/cortex/test_rest_scopes.py::test_scope_missing_403, tests/cortex/test_rest_scopes.py::test_unauthenticated_401, tests/cortex/test_rest_api.py::test_unauthenticated_requests_rejected, tests/security/test_canvas_guard_service_key.py::test_scope_enforcement_still_exists_for_the_exempted_principal
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature

#### Co-Workers canvas (`cwk.canvas`, component `cwk`)

- **GUI: pass**
  - evidence: nav links /coworkers
  - evidence: page /coworkers renders coworkers/index.html
  - evidence: page /coworkers/ renders coworkers/index.html
  - evidence: registry entry 'cwk'
- **Workflow: n/a**
  - evidence: 0 mutating route(s), none a decision a person takes
- **Twin: gap**
  - gap `twin:no-adapter`: no @register_twin adapter observes cwk.canvas (canvas_key 'cwk' or observes)
- **Architecture: pass**
  - evidence: PM-CWK-01 -> if.cwk.console -> tests/test_component_registry.py::test_get_iqe_path_canvas_cwk_ace_collision_rule
  - evidence: PM-CWK-02 -> cwk.canvas, if.cwk.console -> tests/test_coworkers_canvas.py::test_the_roster_is_personas_then_ace_roles_from_config, tests/test_coworkers_canvas.py::test_a_missing_source_contributes_nothing, tests/test_coworkers_canvas.py::test_the_chat_link_is_a_relative_chat_url_for_that_coworker, tests/test_coworkers_canvas.py::test_the_roster_page_lists_every_coworker_with_its_chat_link, tests/test_coworkers_canvas.py::test_a_roster_that_cannot_load_is_empty_not_a_500, tests/test_coworkers_canvas.py::test_open_chat_redirects_into_the_shared_chat_or_404s, tests/test_coworkers_canvas.py::test_status_counts_the_roster_by_kind
- **AI-native: gap**
  - gap `ai:no-read-tool`: no declared read-only MCP tool serves this feature
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

**Done (icx-cnf-04): 17 architecture gaps to 0.** `args/platform_model/` holds `cortex.yaml`,
`ace.yaml`, `cwk.yaml` and `conformance.yaml`: 23 elements, 10 interfaces and 43 requirements
(`PM-CTX-*`, `PM-ACE-*`, `PM-CWK-*`, `PM-CNF-*`), each citing real tests by pytest node.
`tools/mbse/platform_model.py --seed` writes them into the digital thread as project
`icdev-platform`, where `find_orphans` and `find_gaps` find nothing. The coherence check
`platform_model_trace` is the per-feature RTM gate.

The first pass left five requirements with no test. The second pass wrote them, and the tests
found two defects, fixed here:

| Requirement | Tests | What the tests found |
|---|---|---|
| `PM-ACE-07` abort | `tests/test_ace_abort.py` | An abort wrote no audit row. `ACEController.abort` now appends `abort` to `ace_audit_log` with the actor (the person on the route, `mcp` for the tool). |
| `PM-ACE-10` profiles | `tests/test_ace_profile_generator.py` | `generate_profile` took a caller's spec as-is: a request body could claim `trust_tier: green`, Bash, and a `role_id` of `../../x` that wrote outside the roles directory. The spec is now held to the same allowlists as the model's output and the id is re-slugged. |
| `PM-ACE-13` evaluations | `tests/test_ace_eval_routes.py`, plus existing `test_agent_evaluator.py`, `test_eval_trends.py` | Nothing; existing tests were ungated. |
| `PM-CTX-17` BOM | `tests/cortex/test_rest_bom.py`, plus existing `tests/bom/test_cortex_bom.py` | Nothing; the engine was tested, the route was not. |
| `PM-CWK-02` Co-Workers | `tests/test_coworkers_canvas.py` | Nothing. |

Not changed: `args/agent_approval_policy.yaml` does not list `ace_abort`, so an agent's call falls
to the default tier `unknown`, which halts for approval. The route and the tool themselves stop at
once; whether an agent's abort should skip the gate is a policy decision left to the owner.

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
- icx-cnf-04 (osx-fth-33), built in `~/icdev-wt/icx-cnf-04` on
  `feat/icx-cnf-04-platform-model`, from this branch. The audit now reads `interfaces:` as elements,
  checks a cited test's function (not only its file) and a code entry's symbol. The trace shows on
  `/lineage` (ICDEV platform model button, `/api/lineage/platform-model`) and through the read-only
  MCP tool `platform_model_trace`; `trace_forward` / `trace_backward` resolve the seeded rows. Fifteen
  ungated test files the model cites passed alone and in the suite and moved from
  `args/ci_test_backlog.txt` into `args/ci_test_files/core.d/icx-cnf-04.txt`, beside the six new
  test files (`backlog_max` 1671 to 1653; local timings in `args/ci_test_timings/icx-cnf-04.json`). Seeding ran only against
  scratch databases.
