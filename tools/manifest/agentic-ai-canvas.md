# Tool Manifest — Agentic AI Design Canvas (AADC)

**Module:** `tools/agentic_ai_canvas/`
**Blueprint route prefix:** `/agentic-ai`
**Feature flag:** `ICDEV_AADC_ENABLED=true`
**DB:** `data/agentic_ai_canvas.db` (SQLite default; `AADC_STORAGE_BACKEND=postgresql` for PG)

---

## Tools

### `tools/agentic_ai_canvas/db/init_db.py`
Initialize AADC database — creates 15 tables, seeds 12 templates and 12 snippets.
```bash
python tools/agentic_ai_canvas/db/init_db.py
```

### `tools/agentic_ai_canvas/agentic_engine.py`
Rule-based compliance assessment engine (no LLM).
- `assess_design(design_id, graph_json, design_meta)` → full result with NIST AI RMF score, OWASP LLM score, autonomy level, MITRE ATLAS threats, findings
- `classify_autonomy(agent_node, nodes, edges)` → int 0–5
- `classify_impact(design_meta)` → (safety_impacting, rights_impacting)
- `check_nist_ai_rmf(nodes, edges, design_meta)` → (findings, score)
- `check_owasp_llm(nodes, edges)` → (findings, score)
- `map_atlas_threats(nodes)` → threat list

### `tools/agentic_ai_canvas/model_layer.py`
Model layer for Agentic Research Pipeline (AADC Template #5). Three composable components:
- `Embedder` — wraps `tools.llm.get_embedding_provider()` → `EmbedResult(query, vector, model)`
- `ReRanker(top_k, rerank_weight)` — wraps `tools.rag.reranker.rerank_results()` → `List[RankedChunk]`
- `SynthesisLLM(max_context_chars)` — `router.invoke("rag_synthesis")` → `SynthesisResult`
- `AgenticResearchPipeline(top_k, rerank_weight, max_context_chars)` — composes all three
  - `run(query, chunks)` → `PipelineResult`
- Blueprint route: `POST /agentic-ai/api/designs/<id>/run-pipeline` — body: `{query, chunks, top_k}`

### `tools/agentic_ai_canvas/agent_layer.py`
Agent layer for Agentic Research Pipeline (AADC Template #5) — the `researcher-agent` node. Orchestrates the `web-search` and `chunker` upstream nodes.
- `WebSearcher.search(query)` → `List[SearchHit]` — Tavily → SerpAPI → DuckDuckGo Lite fallback chain (air-gap unsafe at the scrape tier)
- `TextChunker` — splits raw docs into model-ready chunks
- `ResearchAgent.search(query)` → `ResearchResult(query, chunks, sources, duration_ms, error)` — chunks feed `AgenticResearchPipeline.run()`

### `tools/agentic_ai_canvas/governance_layer.py`
Governance layer for Agentic Research Pipeline (AADC Template #5) — the three governance nodes between Synthesis LLM and final output. Composed in order: `ConfidenceGate → OutputValidator → PipelineAuditLogger`.
- `ConfidenceGate(threshold).evaluate(confidence)` → `ConfidenceGateResult`
- `OutputValidator().validate(answer, error)` → `OutputValidationResult`
- `PipelineAuditLogger` — non-fatal run start/failed audit logging
- `GovernanceLayer(confidence_threshold, design_id, actor, session_id)` — public entry-point consumed by `AgenticResearchPipeline.run()`; `log_started(query)` / `log_failed(query, error)`

### `tools/agentic_ai_canvas/workflow.py`
HITL workflow + loop_engine bridge.
- `seed_hitl_templates()` — inserts AADC HITL templates into `wf_templates`
- `maybe_create_hitl_instance(design_id, safety, rights, project_id)` → wf_instance_id or None
- `get_workflow_status(design_id)` → dict or None
- `is_approved(design_id)` → bool
- `launch_to_kanban(design_id, name, graph_json, project_id)` → {loop_id, task_ids, clusters}

### `tools/agentic_ai_canvas/bus_subscriber.py`
Cross-canvas event bus integration.
- `register()` — subscribes to `sdc.topology_saved` and `odc.source_added`
- `publish_design_saved(design_id, name)` — emits `aadc.design_saved`
- `publish_agent_flagged(design_id, agent_label, level)` — emits `aadc.agent_flagged`

### `tools/agentic_ai_canvas/mcp_sync.py`
AADC → MCP Tool Registry sync — upserts agent/tool nodes from a design into `mcp_tool_registry` so the MCP gateway is aware of them.
- `sync_design_to_mcp(design_id)` → `{"synced": N, "nodes": [labels]}` on success; `{"synced": 0, "error": str}` on failure (non-fatal)
- Syncs node types: `llm`, `llm-local`, `autonomous-agent`, `orchestrator`, `sub-agent`, `researcher-agent`, `writer-agent`, `reviewer-agent`, `mcp-server`, `mcp-gateway`, `tool-chain`, `external-api`
- Supports both SQLite (`INSERT OR REPLACE`) and PostgreSQL (`ON CONFLICT DO UPDATE`) backends via `get_connection()`

### `tools/db/seeds/seed_ai_canvases_aadc.py`
Seed 8 DoD/IC synthetic AADC designs with full assessment, threat model, ATO, risk, red team, lifecycle, scorecard, and deploy gate data.
```bash
python tools/db/seeds/seed_ai_canvases_aadc.py          # idempotent
python tools/db/seeds/seed_ai_canvases_aadc.py --reset  # wipe + reseed
```

### `tools/db/seeds/seed_ai_canvases_all.py`
Combined orchestrator — seeds all AI canvas DoD/IC demo data (AADC, AIMC, AAC, Observatory, KG).
```bash
python tools/db/seeds/seed_ai_canvases_all.py --json        # all 5 steps
python tools/db/seeds/seed_ai_canvases_all.py --reset-all   # wipe + reseed
```

### `tools/agentic_ai_canvas/events.py`
AADC Activity Feed Emitter — writes one row to `aadc_design_events` on each significant canvas action.
- `emit_event(design_id, event_type, actor, metadata)` → bool (True on success, False if table missing or write fails — non-fatal)
- Uses `get_connection()` for SQLite/PostgreSQL compatibility; silently skips if migration hasn't run yet

### `tools/agentic_ai_canvas/checkpoint_manager.py`
Phase 4 — Checkpoint/fork service (LangGraph pattern).
- `save_checkpoint(design_id, graph_json, label, node_id)` → checkpoint dict
- `list_checkpoints(design_id)` → list of checkpoint dicts
- `get_checkpoint(checkpoint_id)` → checkpoint dict or None
- `restore_checkpoint(design_id, checkpoint_id)` → restores live graph to snapshot
- `fork_design(design_id, checkpoint_id, new_name)` → creates independent forked design
- `delete_checkpoint(design_id, checkpoint_id)` → removes checkpoint

### `tools/agentic_ai_canvas/parallel_graph.py`
Phase 4 — Parallel execution group service.
- `create_group(design_id, node_ids, label, color)` → group dict
- `list_groups(design_id)` → list of group dicts
- `update_group(design_id, group_id, node_ids, label)` → updated group dict
- `delete_group(design_id, group_id)` → status dict
- `validate_parallel_paths(nodes, edges)` → list of structural warnings

### `tools/agentic_ai_canvas/observability_nodes.py`
Phase 4 — Observability node assessment (Haystack/OTel pattern).
- `check_observability_coverage(nodes, edges)` → (findings, score or None)
- `get_observability_coverage_map(nodes, edges)` → per-agent trace/span/metrics coverage

### `tools/agentic_ai_canvas/a2a_sandbox.py`
Phase 4 — A2A bridge + sandbox-exec assessment.
- `check_a2a_sandbox(nodes, edges)` → (findings, score or None)

### `tools/agentic_ai_canvas/safety_extensions.py`
Phase 4 — Trusted monitor + PII field-level safety checks.
- `check_safety_extensions(nodes, edges)` → (findings, score or None)

### `tools/agentic_ai_canvas/safety_layer.py`
Circuit breaker safety layer — in-process singleton per design.
- `record_event(design_id, event_type, detail, node_id)` — log anomaly/success/reset
- `get_status(design_id)` → circuit state dict
- `reset_circuit(design_id)` → manual reset

### `tools/agentic_ai_canvas/safety_redundancy.py`
Phase 3 — Safety redundancy graph analysis.
- `analyze_safety_redundancy(nodes, edges)` → `{score, protected_agents, unprotected_agents, coverage_map, safety_chains, total_agents}`
- Protected = agent with at least one safety/governance predecessor upstream. Score = protected / total × 100.

### `tools/agentic_ai_canvas/coordination_matrix.py`
Phase 3 — Multi-agent coordination matrix builder.
- `build_coordination_matrix(nodes, edges)` → `{agents, matrix, topology, hub_nodes, isolated_agents}`
- Topology: mesh / hub-spoke / pipeline / hierarchical / single / none.

### `tools/agentic_ai_canvas/model_provenance.py`
Phase 3 — Model provenance chain tracker.
- `extract_provenance_chain(nodes)` → list of `{node_id, label, type, model_source, training_data, model_version, model_license}`
- `get_compliance_flags(chain)` → flags for proprietary models, GPL licenses, missing training data.

### `tools/agentic_ai_canvas/simulation_engine.py`
Phase 3 — Agent behavior simulation engine (BFS trace).
- `simulate_execution(nodes, edges, start_node_id, input_payload, max_steps=50)` → `{trace, decisions, halted_by, steps_count, status}`
- Halts at hitl-gate / approval-workflow / circuit-breaker nodes; marks filter nodes as "filtered" but continues.

### `tools/agentic_ai_canvas/red_team.py`
Phase 7 — AI Red Team Engine (12 MITRE ATLAS scenarios).
- `run_red_team(nodes, edges)` → `{scenarios, summary, attack_surface}`
- Exploitability score 0–10 per scenario; mitigated/exposed per node type presence.
- Attack surface flags: no_input_guard, no_output_guard, no_hitl, no_pii_guard, unsandboxed_exec.

### `tools/agentic_ai_canvas/auto_recommend.py`
Phase 7 — Design Linter / Auto-Recommendation Engine (13 rules).
- `lint_design(nodes, edges, design_meta)` → `{recommendations, node_warnings, lint_score, summary}`
- Per-node warnings keyed by node_id for canvas overlay.
- Lint score: 100 − Σ(penalty per rule severity).

### `tools/agentic_ai_canvas/accred_package.py`
Phase 7 — Accreditation Package Builder.
- `build_accred_zip(design, assessment, risks, threat_model, ato, reg, red_team, exec, oscal)` → ZIP bytes
- Assembles 8+ JSON artifacts + README cover sheet into a single downloadable ZIP.

### `tools/agentic_ai_canvas/canvas_bridge.py`
AADC↔AIMC cross-canvas bridge — links AADC agent/model nodes to the AIMC FOUNDATION_MODELS catalog.
- `get_aimc_catalog()` → full FOUNDATION_MODELS list from `tools.aiml_canvas.constants`
- `link_model_node(aadc_design_id, aadc_node_id, aimc_model_id, aimc_design_id, notes)` → upsert into `aadc_aimc_model_refs`; returns stored ref dict with model metadata
- `get_model_refs(aadc_design_id)` → list of refs with joined model metadata and IL status
- `get_aadc_refs_for_model(aimc_model_id)` → AADC designs that reference a given AIMC model
- `check_il_compatibility(aadc_design_id, target_il)` → list of IL violation dicts (CAT1); empty = compliant
- `unlink_model_node(ref_id)` → True if deleted; uses `aadc_aimc_model_refs` table

### `tools/agentic_ai_canvas/ft_linkage.py`
Phase 2 — Fine-tuning dashboard linkage.
- `get_fine_tuning_summary(design_id)` → summary of fine-tuning jobs linked to a design
- Surfaces AADC assessment findings as training signal candidates in the fine-tuning dashboard.

### `tools/agentic_ai_canvas/pattern_detector.py`
Phase 8 — Architectural Pattern Detector (8 named AI design patterns).
- `detect_patterns(nodes, edges)` → `{patterns, dominant, flags}`
- Patterns: BASIC_RAG, AGENTIC_RAG, AUTONOMOUS_AGENT, HITL_SUPERVISED, MULTI_AGENT_ORCHESTRATOR, SAFETY_FIRST, PIPELINE_CHAIN, COGNITIVE_ARCHITECTURE
- Returns confidence score (0-100) per pattern plus matched flags and missing-node suggestions.

### `tools/agentic_ai_canvas/impact_analyzer.py`
Phase 8 — Cascade Impact Analyzer.
- `analyze_impact(nodes, edges)` → `{node_impacts, summary}`
- Per-node: blast_radius (downstream count), is_spof (single point of failure), vulnerability_score (1-8), resilience_reduction (%)
- Summary: resilience_score, critical_nodes, spofs, overall_risk_level (CRITICAL/HIGH/MEDIUM/LOW)

### `tools/agentic_ai_canvas/analytics_engine.py`
Phase 8 — Portfolio Analytics Engine (cross-design intelligence).
- `compute_analytics(designs, assessments, pattern_reports, ato_reports, red_team_reports, lint_reports, risk_items)` → analytics dict
- Computes: 8-week score trend, pattern distribution, compliance drift (30d), risk density by domain, ATO readiness rate, red team risk distribution, lint score distribution.

### `tools/agentic_ai_canvas/scorecard.py`
Phase 9 — Unified Design Scorecard (8-dimension weighted health score).
- `build_scorecard(design, assessment, ato_data, reg_data, red_team_data, lint_data, impact_data, risk_items)` → scorecard dict
- Dimensions: Assessment (25%), ATO (20%), Regulatory (15%), Red Team Resilience (15%), Lint (10%), Structural Resilience (10%), Risk Posture (5%)
- Health labels: HEALTHY (≥80) / AT_RISK (≥60) / DEGRADED (≥40) / CRITICAL (<40)

### `tools/agentic_ai_canvas/deploy_gate.py`
Phase 9 — Deployment Gate (CI/CD readiness verdict + downloadable YAML).
- `run_deploy_gate(design, assessment, ato_data, reg_data, red_team_data, lint_data, impact_data, risk_items)` → gate dict
- Verdicts: APPROVED / CONDITIONAL / BLOCKED
- Hard blockers: CRITICAL unmitigated red team, ATO <40, Lint <40, Resilience CRITICAL, Assessment <40
- `gate_yaml` field → downloadable YAML for GitLab CI / GitHub Actions pipeline integration

### `tools/agentic_ai_canvas/findings_inbox.py`
Phase 9 — Unified Findings Inbox (cross-analysis findings aggregator).
- `aggregate_findings(designs, assessments, lint_reports, red_team_reports, ato_reports, regulatory_reports, risk_items, ...)` → {findings, summary, filters}
- Sources: assessment findings, lint issues, red team unmitigated scenarios, ATO failures, regulatory gaps, open CRITICAL/HIGH risk items
- Filterable by severity, source, design_id; sorted by severity descending.

### `tools/agentic_ai_canvas/lifecycle_manager.py`
Phase 10 — Design Lifecycle State Machine.
- `get_lifecycle(design_id, conn)` → {current_state, history, available_transitions, state_colors}
- `transition(design_id, to_state, actor, reason, conn)` → {ok, new_state/error}
- States: DRAFT → UNDER_REVIEW → APPROVED → DEPLOYED → DEPRECATED (+ CHANGES_REQUESTED branch)
- APPROVED and DEPLOYED transitions flagged as requiring deploy gate check.

### `tools/agentic_ai_canvas/review_workflow.py`
Phase 10 — Design Review Workflow (multi-reviewer comments/decisions).
- `get_review(design_id, conn)` → {comments, status, reviewer_summary, type_colors}
- `add_comment(design_id, reviewer, comment_type, body, node_id, conn)` → {ok, comment_id}
- Comment types: COMMENT / APPROVAL / CHANGE_REQUEST / REJECTION
- Derived status: PENDING → APPROVED / CHANGES_REQUESTED / REJECTED

### `tools/agentic_ai_canvas/monitoring_engine.py`
Phase 10 — Portfolio Monitoring Engine (score drift + alerts).
- `compute_monitoring(designs, assessments)` → {design_alerts, summary, generated_at}
- Per-design: current_score, baseline_score, drift, alert_level (CRITICAL/HIGH/MEDIUM/OK), last-10 history
- Alert thresholds: CRITICAL ≥20pts drop, HIGH ≥10pts, MEDIUM ≥5pts

### `tools/agentic_ai_canvas/solution_packs.py`
Phase 5 — Solution Packs (7 pre-wired domain-specific agentic AI templates).
- `build_packs()` → list of dicts ready for `aadc_templates` DB seeding; each includes graph_json, compliance_badges, autonomy_max, and risk register seeds
- `recommend_pack(domain, goal, autonomy)` → pack name from quick-start routing matrix (domain × goal × autonomy → best-fit pack)
- `SOLUTION_PACK_RISKS` — per-pack pre-seeded risk register items (PII, prompt injection, supply chain, etc.)
- `QUICKSTART_ROUTES` — 30-entry routing matrix covering government, healthcare, technology, financial, and general domains
- Packs: Customer Service Agent, Autonomous Coder, Knowledge Research Agent, Cybersecurity SOC Agent, Healthcare Admin Agent, Gov/Procurement Agent, Multi-Agent Research Lab

### `tools/agentic_ai_canvas/ato_readiness.py`
Phase 6 — ATO Readiness Checker (15 items across FedRAMP / OMB M-25-21 / DoD AI Ethics / CMMC L2).
- `run_ato_checklist(nodes, design_meta)` → `{items, summary, by_framework}`
- Domain-filtered: safety_impacting and rights_impacting items only appear when relevant.

### `tools/agentic_ai_canvas/regulatory_tracker.py`
Phase 6 — Regulatory Gap Analysis (14 reqs: EU AI Act / DoD AI Ethics / OMB M-25-21 / OMB M-26-04).
- `run_regulatory_analysis(nodes, design_meta, risk_items)` → `{gaps, summary, by_framework}`
- Checks node presence, risk register population, provenance documentation, name/description.

### `tools/agentic_ai_canvas/design_compare.py`
Phase 6 — Two-design comparison engine.
- `compare_designs(design_a, design_b, assessment_a, assessment_b, risks_a, risks_b)` → delta dict
- Fields: node_delta, score_delta (overall/nist/owasp), risk_delta, autonomy_delta, verdict.

### `tools/agentic_ai_canvas/exec_summary.py`
Phase 6 — Executive Summary Report generator.
- `generate_exec_summary(design, assessment, risks, threat_model, ato_result, reg_result)` → summary dict
- Combined posture score: 50% assessment + 30% ATO + 20% regulatory.
- Posture ratings: EXCELLENT / GOOD / FAIR / POOR / UNRATED.

### `tools/agentic_ai_canvas/risk_register.py`
Phase 5 — Risk register CRUD + finding importer.
- `finding_to_risk(finding)` → draft risk item dict from assessment finding
- `risk_score(severity, likelihood)` → int 1–25 composite score
- `summarize_register(risk_items)` → `{total, open, critical_open, by_severity, by_status, residual_risk}`

### `tools/agentic_ai_canvas/threat_model.py`
Phase 5 — STRIDE + ATLAS threat model generator.
- `generate_threat_model(nodes, edges)` → `{stride, atlas_threats, threat_count, high_count, stride_summary}`
- 11 STRIDE rules across 6 categories; ATLAS TTPs mapped per node type.
- `_stride_summary(threats)` → `{category: count}`

### `tools/agentic_ai_canvas/portfolio.py`
Phase 5 — Portfolio analytics aggregator (cross-design).
- `aggregate_portfolio(designs, assessments, risk_items)` → `{total_designs, avg_score, compliance_bands, open_risks, critical_open_risks, portfolio_health, ...}`
- Health states: `AT_RISK` / `COMPLIANT` / `IMPROVING` / `NON_COMPLIANT`.

### `tools/agentic_ai_canvas/oscal_export.py`
Phase 5 — OSCAL 1.1 Component Definition export.
- `export_oscal_component(design, graph, assessment)` → OSCAL Component Definition dict (serialize to JSON)
- `get_control_coverage_summary(nodes)` → `{controls_covered, families, control_ids}`
- Maps 25 node types to NIST SP 800-53 Rev 5 controls.

### `tools/agentic_ai_canvas/blueprint.py`
Flask Blueprint (`aadc_bp`) — all routes registered under `/agentic-ai`.

**Page routes:**
| Route | Template |
|-------|----------|
| `GET /agentic-ai/` | `agentic_ai_canvas/index.html` |
| `GET /agentic-ai/canvas/new` | `agentic_ai_canvas/canvas.html` |
| `GET /agentic-ai/canvas/<id>` | `agentic_ai_canvas/canvas.html` |
| `GET /agentic-ai/templates` | `agentic_ai_canvas/templates.html` |
| `GET /agentic-ai/snippets` | `agentic_ai_canvas/snippets.html` |
| `GET /agentic-ai/canvas/<id>/assessments` | `agentic_ai_canvas/assessments.html` |
| `GET /agentic-ai/canvas/<id>/artifacts` | `agentic_ai_canvas/artifacts.html` |
| `GET /agentic-ai/risks/<id>` | `agentic_ai_canvas/risks.html` |
| `GET /agentic-ai/red-team/<id>` | `agentic_ai_canvas/red_team.html` |
| `GET /agentic-ai/ato/<id>` | `agentic_ai_canvas/ato.html` |
| `GET /agentic-ai/exec-summary/<id>` | `agentic_ai_canvas/exec_summary.html` |
| `GET /agentic-ai/patterns/<id>` | `agentic_ai_canvas/pattern_analysis.html` |
| `GET /agentic-ai/impact/<id>` | `agentic_ai_canvas/impact_analysis.html` |
| `GET /agentic-ai/analytics` | `agentic_ai_canvas/analytics.html` |
| `GET /agentic-ai/scorecard/<id>` | `agentic_ai_canvas/scorecard.html` |
| `GET /agentic-ai/deploy-gate/<id>` | `agentic_ai_canvas/deploy_gate.html` |
| `GET /agentic-ai/findings` | `agentic_ai_canvas/findings.html` |
| `GET /agentic-ai/lifecycle/<id>` | `agentic_ai_canvas/lifecycle.html` |
| `GET /agentic-ai/review/<id>` | `agentic_ai_canvas/review.html` |
| `GET /agentic-ai/monitoring` | `agentic_ai_canvas/monitoring.html` |
| `GET /agentic-ai/impact-graph` | `agentic_ai_canvas/impact_graph.html` |

**API routes:**
| Method + Route | Purpose |
|----------------|---------|
| `POST /agentic-ai/api/designs` | Create new design |
| `GET /agentic-ai/api/designs/<id>` | Get design + last assessment |
| `PUT /agentic-ai/api/designs/<id>` | Save design (triggers assess + HITL + event bus) |
| `DELETE /agentic-ai/api/designs/<id>` | Delete design |
| `POST /agentic-ai/api/designs/<id>/assess` | Run rule-based assessment |
| `POST /agentic-ai/api/designs/<id>/launch` | Launch design to Kanban via loop_engine |
| `GET /agentic-ai/api/templates` | List all 12 templates |
| `POST /agentic-ai/api/templates/<tid>/apply/<did>` | Apply template → new design |
| `POST /agentic-ai/api/designs/<id>/save-as-template` | Save current design as custom template |
| `GET /agentic-ai/api/snippets` | List all 12 snippets |
| `POST /agentic-ai/api/designs/<id>/snippets/<sid>` | Insert snippet into design graph |
| `GET /agentic-ai/api/designs/<id>/artifacts` | List generated artifacts |
| `POST /agentic-ai/api/designs/<id>/artifacts` | Generate model_card / system_card / ai_bom |
| `GET /agentic-ai/api/designs/<id>/checkpoints` | List checkpoints |
| `POST /agentic-ai/api/designs/<id>/checkpoints` | Save current graph as checkpoint |
| `POST /agentic-ai/api/designs/<id>/checkpoints/<cid>/restore` | Restore design to checkpoint |
| `POST /agentic-ai/api/designs/<id>/checkpoints/<cid>/fork` | Fork new design from checkpoint |
| `DELETE /agentic-ai/api/designs/<id>/checkpoints/<cid>` | Delete checkpoint |
| `GET /agentic-ai/api/designs/<id>/parallel-groups` | List parallel groups |
| `POST /agentic-ai/api/designs/<id>/parallel-groups` | Create parallel group |
| `PUT /agentic-ai/api/designs/<id>/parallel-groups/<gid>` | Update group |
| `DELETE /agentic-ai/api/designs/<id>/parallel-groups/<gid>` | Delete group |
| `POST /agentic-ai/api/designs/<id>/validate-parallel` | Validate fork/join structure |
| `GET /agentic-ai/api/designs/<id>/safety-redundancy` | Analyze + cache safety coverage |
| `GET /agentic-ai/api/designs/<id>/coordination-matrix` | Build N×N agent coordination matrix |
| `GET /agentic-ai/api/designs/<id>/provenance` | Extract model provenance chain + flags |
| `PUT /agentic-ai/api/designs/<id>/nodes/<nid>/provenance` | Save provenance fields to a node |
| `POST /agentic-ai/api/designs/<id>/simulate` | Run BFS simulation + persist trace |
| `GET /agentic-ai/api/designs/<id>/simulations` | List recent simulation runs |
| `GET /agentic-ai/api/designs/<id>/risks` | List risk items |
| `POST /agentic-ai/api/designs/<id>/risks` | Create risk item |
| `PUT /agentic-ai/api/designs/<id>/risks/<rid>` | Update risk item |
| `DELETE /agentic-ai/api/designs/<id>/risks/<rid>` | Delete risk item |
| `POST /agentic-ai/api/designs/<id>/risks/import-findings` | Import findings from latest assessment as risks |
| `GET /agentic-ai/api/designs/<id>/threat-model` | Get latest threat model |
| `POST /agentic-ai/api/designs/<id>/threat-model` | Generate + persist STRIDE + ATLAS threat model |
| `GET /agentic-ai/api/portfolio` | Portfolio analytics (all designs) |
| `GET /agentic-ai/api/designs/<id>/oscal` | Export OSCAL 1.1 Component Definition JSON |
| `GET /agentic-ai/api/designs/<id>/oscal/control-coverage` | NIST 800-53 control coverage summary |
| `GET /agentic-ai/api/designs/<id>/ato` | ATO readiness checklist JSON |
| `GET /agentic-ai/api/designs/<id>/regulatory` | Regulatory gap analysis JSON |
| `GET /agentic-ai/api/designs/<id>/exec-summary` | Executive summary report JSON |
| `POST /agentic-ai/api/designs/compare` | Compare two designs side-by-side |
| `GET /agentic-ai/api/designs/<id>/red-team` | Red team adversarial analysis JSON |
| `GET /agentic-ai/api/designs/<id>/lint` | Design lint report JSON |
| `GET /agentic-ai/api/designs/<id>/accred-package` | Accreditation package ZIP download |
| `GET /agentic-ai/api/designs/<id>/patterns` | Architectural pattern detection JSON |
| `GET /agentic-ai/api/designs/<id>/impact` | Cascade impact analysis JSON |
| `GET /agentic-ai/api/analytics` | Portfolio analytics JSON |
| `GET /agentic-ai/api/designs/<id>/scorecard` | Unified 8-dimension scorecard JSON |
| `GET /agentic-ai/api/designs/<id>/deploy-gate` | Deployment gate verdict JSON |
| `GET /agentic-ai/api/designs/<id>/deploy-gate/download` | Gate check YAML download |
| `GET /agentic-ai/api/findings` | Portfolio-wide findings feed JSON |
| `GET /agentic-ai/api/designs/<id>/lifecycle` | Lifecycle state + history JSON |
| `POST /agentic-ai/api/designs/<id>/lifecycle/transition` | Execute lifecycle state transition |
| `GET /agentic-ai/api/designs/<id>/review` | Review comments + status JSON |
| `POST /agentic-ai/api/designs/<id>/review` | Add review comment/decision |
| `GET /agentic-ai/api/monitoring` | Portfolio monitoring alerts JSON |

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `aadc_designs` | Design metadata (name, domain, classification, graph_json) |
| `aadc_assessments` | Per-assessment snapshots (NIST/OWASP/Phase4 scores, findings, ATLAS) |
| `aadc_templates` | 12 built-in + user custom templates with full graph JSON |
| `aadc_snippets` | 12 reusable subgraph patterns |
| `aadc_artifacts` | Generated model cards, system cards, AI BOMs (markdown) |
| `aadc_workflow_links` | Maps design_id → wf_instance_id for HITL tracking |
| `aadc_loop_links` | Maps design_id → loop_id for Kanban/loop_engine tracking |
| `aadc_audit_log` | Append-only audit trail (NIST AU-2) |
| `aadc_design_tags` | Tag associations for designs |
| `aadc_checkpoints` | Phase 4 — checkpoint/fork state snapshots (migration 105) |
| `aadc_parallel_groups` | Phase 4 — named parallel execution swim-lanes (migration 105) |
| `aadc_safety_graphs` | Phase 3 — safety redundancy snapshots per design (migration 106) |
| `aadc_agent_simulations` | Phase 3 — agent behavior simulation traces (migration 106) |
| `aadc_risk_items` | Phase 5 — risk register items per design (migration 107) |
| `aadc_threat_models` | Phase 5 — STRIDE + ATLAS threat model snapshots per design (migration 107) |
| `aadc_red_team_reports` | Phase 7 — AI red team report snapshots per design (migration 109) |
| `aadc_lint_reports` | Phase 7 — Design lint report snapshots per design (migration 109) |
| `aadc_ato_reports` | Phase 6 — ATO readiness report snapshots per design (migration 108) |
| `aadc_regulatory_gaps` | Phase 6 — Regulatory gap analysis snapshots per design (migration 108) |
| `aadc_pattern_reports` | Phase 8 — Architectural pattern detection snapshots per design (migration 110) |
| `aadc_impact_reports` | Phase 8 — Cascade impact analysis snapshots per design (migration 110) |
| `aadc_scorecard_snapshots` | Phase 9 — Unified design scorecard snapshots per design (migration 111) |
| `aadc_deploy_gates` | Phase 9 — Deployment gate verdict snapshots per design (migration 111) |
| `aadc_lifecycle_states` | Phase 10 — Design lifecycle state transition log per design (migration 112) |
| `aadc_review_comments` | Phase 10 — Design review comments and decisions per design (migration 112) |
| `aadc_design_events` | Activity feed — one row per significant canvas action emitted by `events.py` |

---

## Node Palette (60+ types across 8 categories)

| Category | Example nodes |
|----------|--------------|
| Model | LLM Inference, Foundation Model, Fine-Tuned Model, Embedding Model, VLM, STT, TTS |
| Memory | Vector Store, Chat History, Episodic Memory, Working Memory, KG Memory, Long-Term Memory |
| Agent | Autonomous Agent, HITL Agent, Orchestrator, Subagent, Researcher, Planner, Executor |
| Tool/MCP | MCP Server, Web Search, Code Executor, File System, API Connector, Database Tool |
| Data | Document Corpus, Data Pipeline, RAG Retriever, Training Dataset, Feedback Dataset |
| Safety | Guardrail, Output Filter, PII Redaction, Toxicity Filter, Rate Limiter, Circuit Breaker |
| Governance | Audit Logger, Compliance Monitor, HITL Gate, Policy Engine, Explainability Module |
| Infra | API Gateway, Message Queue, Batch Processor, Cache Layer, LLM Proxy, Telemetry Sink |

---

## Templates (12 built-in)

1. Simple RAG Q&A (L0, NIST 88%)
2. HITL-Gated RAG (L1, NIST 96%)
3. Single Autonomous Agent (L2, OWASP 80%)
4. Multi-Agent Orchestrator (L3, NIST 79%)
5. Agentic Research Pipeline (L3, NIST 82%)
6. Proposal Genesis Pattern (L2, NIST 91%)
7. RAG + Fine-Tuning Loop (L1, NIST 88%)
8. MCP Server Cluster (L2, OWASP 85%)
9. Compliance-First AI (L1, NIST 98%)
10. Digital Twin AI (L3, NIST 75%)
11. Conversational Intake Agent (L1, NIST 93%)
12. AI Security Monitor (L2, NIST 95%)

---

## Snippets (12 reusable patterns)

1. Basic RAG Retrieval (3 nodes)
2. HITL Approval Gate (2 nodes)
3. Safety Guardrail Stack (3 nodes)
4. Confidence + Circuit Breaker (2 nodes)
5. MCP Tool Surface (4 nodes)
6. Agent Memory System (3 nodes)
7. Feedback Loop (3 nodes)
8. Behavioral Baseline (2 nodes)
9. Agent Spawn Pattern (3 nodes)
10. Token Budget Enforcer (2 nodes)
11. Prompt Registry Lookup (2 nodes)
12. AI Audit Trail (2 nodes)

---

## Compliance Frameworks Assessed

- **NIST AI RMF** (Govern/Map/Measure/Manage) — 8 weighted checks, 0–100% score
- **OWASP LLM Top 10** — 10 checks covering prompt injection, data poisoning, etc.
- **OMB M-25-21** — safety-impacting + rights-impacting domain classification
- **MITRE ATLAS** — adversarial ML technique mapping per node type
- **NIST AI 600-1** — high-risk AI system indicators

---

---

## Enhancement Modules (Phase E — Canvas JS, Cost, IaC, Impact Graph)

### `tools/agentic_ai_canvas/cost_estimator.py`
Live cost estimation — token budget calculator, model-specific pricing, optimization hints.
- `estimate_design_cost(graph, runs_per_month=1000)` → `{model_breakdown, total_per_run, total_monthly, runs_per_month, optimization_hints, has_local_models}`
- Priced models: claude-opus-4, claude-sonnet-4, gpt-4o, gpt-4o-mini, llama-3.3-70b, llama-3.1-8b, mistral-large, gemini-1.5-pro, gemini-1.5-flash, qwen3-local (free), ollama-local (free)
- Hints: swap expensive→cheaper if score ≥80; shared-proxy hint for N agents on same model; local-savings hint when Ollama detected
- Persists results to `aadc_cost_estimates` table; wired as `POST /api/agentic-ai/designs/<id>/cost-estimate`

### `tools/agentic_ai_canvas/iac_generator.py`
One-click IaC export — Terraform HCL + Kubernetes Helm chart bundle from design graph.
- `generate_deploy_bundle(graph, name, target_csp="auto", options=None)` → `{files: [{path, content}], manifest, summary, zip_bytes}`
- Node→resource mapping: `agent-*` → `kubernetes_deployment`, `llm-local` → `helm_release` (Ollama), `vector-db` → `helm_release` (Chroma/Weaviate), `mcp-server` → `kubernetes_service`, `infra-k8s` → `kubernetes_namespace`, model nodes → provider ConfigMaps
- Output ZIP: `{name}-iac/terraform/{main,variables,outputs,providers}.tf` + `helm/{Chart.yaml,values.yaml,templates/deployment-*.yaml,service-*.yaml,configmap-*.yaml}` + `README.md`
- Wired as `GET /api/agentic-ai/designs/<id>/iac` (streams ZIP response)

### `tools/dashboard/static/js/agentic-canvas.js`
Dedicated AADC canvas JS — extends `design-canvas.js` globals without replacing them.
- Autonomy level badges (L0–L5) on every agent node using `AUTONOMY_COLORS`
- HITL path highlighter — edges from assessment `hitl_paths` colored green; L3+ without HITL colored red
- Per-node compliance overlay circles (0–100) with click → findings tooltip (NIST/OWASP checks)
- Threat count badges with hover → ATLAS threat popover
- Risk register slide-in sidebar (320px) — severity-grouped risks + inline "Mitigate" button
- Cost panel in right sidebar — $/run, $/month breakdown + "Optimize" button
- Toolbar buttons injected into `#aadc-toolbar-extra`: 💰 Cost, ⚙ IaC, 🛡 Risks
- Listens for `aadc:assessment:complete` custom event to refresh all overlays

### `tools/dashboard/templates/agentic_ai_canvas/impact_graph.html`
Cross-design impact graph page — Sigma.js force graph with blast-radius analysis.
- `/agentic-ai/impact-graph` — two-panel layout (Sigma canvas + info panel)
- Nodes colored by autonomy level, sized by blast_radius count
- 50-iteration spring force layout (repulsion + attraction) for meaningful positioning
- Blast-radius sidebar — lists downstream designs at risk if this node is compromised
- Risk summary bar — total designs, high-risk count, max blast radius
- Add-link / delete-link forms for managing cross-design dependencies
- API: `GET /api/agentic-ai/impact-graph` returns `{nodes, edges, risk_summary}`; DFS computes blast_radius per node

---

## New API Routes (Phase E)

| Method + Route | Purpose |
|----------------|---------|
| `POST /api/agentic-ai/designs/<id>/cost-estimate` | Run cost estimator, persist to `aadc_cost_estimates`, return breakdown |
| `GET /api/agentic-ai/designs/<id>/iac` | Generate + stream Terraform+Helm ZIP |
| `GET /api/agentic-ai/designs/<id>/links` | List design-to-design links |
| `POST /api/agentic-ai/designs/<id>/links` | Create design-to-design link |
| `DELETE /api/agentic-ai/designs/<id>/links/<lid>` | Remove a design link |
| `GET /agentic-ai/impact-graph` | Impact graph page |
| `GET /api/agentic-ai/impact-graph` | Impact graph JSON data |

---

## New Constants (Phase E — `constants.py`)

| Constant | Purpose |
|----------|---------|
| `AUTONOMY_COLORS` | L0–L5 → hex color map (green to purple) |
| `AADC_MODEL_COSTS` | 11 models → `{input, output, avg_in, avg_out}` pricing dict |
| `AADC_IAC_NODE_MAP` | 30 node type prefixes → `(tf_resource, helm_template)` tuples |

---

## New DB Tables (Phase E)

| Table | Purpose |
|-------|---------|
| `aadc_design_links` | Cross-design dependency edges (src→tgt, link_type, auto_detected) |
| `aadc_cost_estimates` | Persisted cost estimate snapshots per design |

---

## Static Assets

| File | Purpose |
|------|---------|
| `tools/dashboard/static/css/agentic-canvas.css` | Node styling, palette, props panel, toolbar |
| `tools/dashboard/static/js/agentic-canvas.js` | Phase E — autonomy badges, HITL highlights, overlays, risk sidebar, cost panel |

---

## Registration Points (all 7 complete)

1. `tools/agentic_ai_canvas/blueprint.py` — Flask Blueprint `aadc_bp`
2. `tools/dashboard/app.py` `_CANVAS_DEFS` — `("aadc", "ICDEV_AADC_ENABLED", ...)`
3. `tools/dashboard/app.py` `inject_globals` — `"aadc_enabled": _CANVAS_FLAGS.get("aadc", False)`
4. `tools/canvas/orchestrator.py` `VALID_CANVAS_KEYS` — `"aadc"`
5. `tools/canvas/orchestrator.py` `CANVAS_DB_MAP` — `"aadc": ("agentic_ai_canvas.db", ...)`
6. `tools/dashboard/templates/base.html` — nav dropdown under `{% if aadc_enabled %}`
7. `tools/manifest/agentic-ai-canvas.md` — this file
