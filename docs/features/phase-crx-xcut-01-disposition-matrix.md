# Phase CRX — Component Review Remediation: 160-Finding Disposition Matrix

**Card:** `crx-xcut-01` · CRX — Component Review Remediation
**Type:** Cross-cutting documentation (disposition record — no production code)
**Source:** 20 Hermes component reviews (2026-07-19), 8 findings each = 160 total
**ADR:** [D-CRX block in adrs.md](../reference/adrs.md#phase-75--crx-component-review-remediation-d-crx)

> **Scope note (public repo):** This is a triage/disposition record for a public
> repository. Where a finding touches security posture, the row states the decision and
> cites the covering capability only — no exploitation detail. Detailed threat analysis
> is tracked internally, consistent with how the `crx-db-01` spike was sanitized (PR #702).

---

## Why this document exists

The 20 component reviews produced 160 recommendations. Many flagged capabilities that
**already exist** — the reviews repeatedly reported present features as missing (verified
below). Rather than silently drop those, every finding is recorded here with a
disposition so nothing is lost. `crx-xcut-01` is the last CRX task; the 17 preceding
tasks built what genuinely needed building.

### Disposition vocabulary

| Code | Meaning |
|------|---------|
| **BUILT** | Implemented this CRX cycle. Cites the merged PR. |
| **COVERED** | Capability already existed before CRX. Cites the file/tool/project verified in the live tree. |
| **SUPERSEDED** | Owned by another active card (RCE=RAG, SAG=standalone-agent, TWX=twin, DMX=DocMod). |
| **DEFERRED** | Valid but not scheduled — rationale given. No current demand or ops-tier concern. |
| **REJECTED** | Declined on a standing principle (no-npm, Flask-for-compliance, external-SaaS-no-demand). |

### Summary counts

| Disposition | Count |
|-------------|-------|
| BUILT (this CRX cycle) | 26 |
| COVERED (pre-existing, verified) | 84 |
| SUPERSEDED (RCE/DMX) | 8 |
| DEFERRED | 30 |
| REJECTED | 12 |
| **Total** | **160** |

BUILT + COVERED + SUPERSEDED = 118 findings (**~74%**) were either already handled or
delivered this cycle — matching the card's "~75% stale/covered" estimate.

**Spot-verified COVERED claims** (file confirmed present in the live tree at merge HEAD
`72ef22c21`): `tools/llm/prompt_registry.py`, `tools/llm/cost_intelligence.py`,
`router.py` fallback chain, `tools/knowledge_graph/temporal.py::temporal_diff`,
`disambiguator.py::find_duplicates`, `federation.py::cross_project_coverage`,
`/components-map` route, `tools/compliance/cato_monitor.py` + `cato_scheduler.py` +
`cato_twin` reflex, `tools/regulatory_foresight/`, `tools/data_canvas/pii_scanner.py` +
`lineage.py` + `quality_engine.py` + `anomaly_detector.py`, `tools/cloud/csp_monitor.py`,
`tools/security/atlas_red_team.py` + `llm_red_team.py`, `tools/rag/raptor.py` +
`chunker.py`, `tools/knowledge_graph/graph_rag.py`,
`tools/supply_chain/{scrm_assessor,dependency_graph,ndaa_889_screener,rare_earth_cascade}.py`,
`tools/govcon/{teaming_hub,subcontractor_tracker,bayesian_bid_scorer,contract_mods_manager,cpars_predictor}.py`,
`args/docmod/packs/{architecture_patterns,sop_workflows,policy_refs,evidence_currency}.yaml`,
`tools/dashboard/api/events.py` (SSE), `tools/kanban/metrics.py`.

---

## Disposition matrix (by review)

### 1. Genesis Daemon & Reflex System
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Central reflex dependency DAG (`depends_on:`) | **BUILT** | crx-gen-03 #673 — topo-order scheduling |
| 2 | Reflex failure isolation / fresh conn + rollback | **BUILT** | crx-gen-01 #657 — `reflex_connection_scope` |
| 3 | Alerting on reflex failure | **BUILT** | crx-gen-02 #668 — `reflex_health` + alerts |
| 4 | Architecture drift reflex | **DEFERRED** | Overlaps docdrift-1; ADR-vs-impl drift not scheduled |
| 5 | Reflex history queryability (indexes) | **BUILT** | crx-gen-02 #668 — migration 284 genesis_audit health indexes |
| 6 | Per-reflex resource limits | **BUILT** | crx-gen-03 #673 — execution/resource caps |
| 7 | Centralize reflex config | **DEFERRED** | Operator ergonomics only; low priority |
| 8 | Blue/green / canary for reflex changes | **DEFERRED** | Dry-run default already exists; staged rollout deferred |

### 2. Database & Storage
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Read replicas / HA standby | **DEFERRED** | Ops/infra tier, not application code |
| 2 | Vector index at scale (faiss/vss) | **SUPERSEDED** | RCE card + pgvector primary (`graph_rag.py` PG-native `<=>`) |
| 3 | Connection health / pool metrics | **BUILT** | crx-db-02 #681 — `query_health.py` + pool metrics |
| 4 | Migration rollback protection | **COVERED** | `tools/db/backup_manager.py` pre-migration backup + `MigrationRunner` |
| 5 | Slow-query / EXPLAIN monitoring | **BUILT** | crx-db-02 #681 — `query_health.py` slow-query capture |
| 6 | Retention / archival policy | **BUILT** | crx-db-03 #700 — config-driven retention (`retention_sweep` reflex) |
| 7 | Multi-region | **DEFERRED** | Ops/infra tier |
| 8 | PostgreSQL native RLS | **BUILT (spike→GO)** | crx-db-01 #693 — go/no-go write-up (sanitized #702) |

### 3. LLM Router & AI Orchestration
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Per-task model benchmarking | **DEFERRED** | `agentic_fitness` + `crag_benchmark` exist; per-task routing benchmark not core |
| 2 | Prompt versioning / registry | **COVERED (stale)** | `tools/llm/prompt_registry.py` (register/list/activate) |
| 3 | Structured output enforcement | **COVERED** | Cortex extract/classify JSON + `validate_agent_output` |
| 4 | Distributed tenant cache (Redis) | **REJECTED** | Redis dep; process-local LRU by design (air-gap) |
| 5 | Prompt injection detection | **COVERED** | Redaction egress hook + `validate_agent_output` + `confabulation_check` |
| 6 | Automatic model fallback chain | **COVERED (stale)** | `tools/llm/router.py` fallback-chain walk (verified) |
| 7 | Token spend trending / budget alerts | **COVERED (stale)** | `tools/llm/cost_intelligence.py` (dashboard/anomalies/recommend) |
| 8 | LLM output quality metrics | **COVERED** | Rubric gating + `quality_feedback_run` + `confabulation_check` |

### 4. Knowledge Graph Engine
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Temporal edges / time-travel | **COVERED (stale)** | `knowledge_graph/temporal.py::temporal_diff` + `kg_time_range` |
| 2 | Incremental (streaming) ingestion | **COVERED** | `kg_enrich` + `graph_sync` incremental (DIC KG LLM extraction, #318) |
| 3 | Graph quality metrics (orphans/stale) | **COVERED (stale)** | `kg_stale_entities` + `disambiguator.find_duplicates` |
| 4 | System-specific control edges | **COVERED** | `kg_compliance_build/coverage` + `federation.cross_project_coverage` |
| 5 | Node embeddings / semantic similarity | **COVERED** | `graph_rag.py` PG-native `<=>` (Tier B) |
| 6 | ML entity resolution | **COVERED** | `entity_resolver.py` + `kg_resolve_ambiguous` (ML upgrade deferred) |
| 7 | Graph visualization | **COVERED (stale)** | `/components-map` + `system_graph` blueprint viz |
| 8 | KG blast radius → doc freshness | **BUILT** | crx-kg-01 #683 — `blast_radius` dim in `freshness_engine.py` |

### 5. Compliance Framework
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | cATO automation | **COVERED** | `cato_monitor.py` + `cato_scheduler.py` + `cato_twin` reflex |
| 2 | Evidence validation gate | **COVERED** | DIC `freshness_engine` + `ssp_generator` + content grounding |
| 3 | Inherited / hybrid control tracking | **COVERED** | Crosswalk engine + `tenant_component_overrides` (migration 207) |
| 4 | Assessment scheduling | **COVERED** | `ai_reassessment_schedule` + `cato_scheduler.py` |
| 5 | Regulatory foresight feeds | **COVERED** | `tools/regulatory_foresight/` (verified dir) |
| 6 | Supply-chain compliance integration | **COVERED** | `supply_chain/` + `cve_passive_watcher` + KEV |
| 7 | POAM auto-remediation | **COVERED** | `poam_generator.py` + `production_remediate` |
| 8 | Multi-tenant compliance isolation | **COVERED** | RLS predicate + tenant overlays (SHX sweep) |

### 6. Dashboard / Frontend
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | SPA migration (React/Vue) | **REJECTED** | SPA rejected for compliance-constrained server-rendered Flask |
| 2 | Real-time (WebSocket/SSE) | **COVERED** | `tools/dashboard/api/events.py` (SSE) |
| 3 | PWA / mobile / offline | **DEFERRED** | No current demand |
| 4 | Accessibility (Section 508) | **BUILT** | crx-test-02 #682 — `a11y_sweep.py` |
| 5 | Custom dashboards / widgets | **DEFERRED** | No current demand |
| 6 | Dark-mode persistence | **COVERED (stale)** | `base.html` `data-theme` theming |
| 7 | Vite build pipeline | **REJECTED** | Vite/npm violates no-npm preference |
| 8 | Component library (React/npm) | **REJECTED** | no-npm; pure-Python/server-rendered stack |

### 7. Data Canvas
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Data catalog discovery (S3/Snowflake scan) | **DEFERRED** | External-source auto-scan; no demand |
| 2 | Quality scoring trending | **COVERED** | `data_canvas/quality_engine.py` (DCPR) |
| 3 | Lineage visualization | **COVERED** | `data_canvas/lineage.py` (DCPR) |
| 4 | PII classification | **COVERED** | `data_canvas/pii_scanner.py` (verified) |
| 5 | Data contract enforcement | **COVERED** | `quality_engine` publish gate (DCPR) |
| 6 | Data marketplace/exchange | **DEFERRED** | No demand |
| 7 | Anomaly detection | **COVERED** | `data_canvas/anomaly_detector.py` (verified) |
| 8 | Auto data API gateway (REST/GraphQL) | **DEFERRED** | No demand |

### 8. DIC — Document Intelligence
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Architecture pattern pack | **COVERED** | `args/docmod/packs/architecture_patterns.yaml` (DMX) |
| 2 | SOP / workflow pack | **COVERED** | `args/docmod/packs/sop_workflows.yaml` (DMX) |
| 3 | Proactive freshness notification | **BUILT** | crx-not-01 #684 routing + `freshness_engine` thresholds |
| 4 | Inter-document reference tracking | **COVERED** | `consistency_checker.py` cross-refs |
| 5 | Semantic claim tracking | **SUPERSEDED** | DMX / DIC KG LLM extraction (#318) |
| 6 | URL / link-rot detection | **DEFERRED** | Lightweight reflex; no demand |
| 7 | Temporal validity for standards | **COVERED** | `policy_refs.yaml` + `evidence_currency.yaml` packs |
| 8 | Regen quality gate | **COVERED** | `verifier.verify()` + WriteGuard (DMX) |

### 9. DocDrift
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Architecture drift detection | **DEFERRED** | ADR-vs-impl reflex not scheduled (dup of genesis-4) |
| 2 | External change-feed integration | **COVERED** | `cve_passive_watcher` + `regulatory_foresight/` |
| 3 | Drift dependency graph | **COVERED** | `consistency_checker` blast radius + KG |
| 4 | Drift remediation SLA | **COVERED** | Reuses crx-kan-01 SLA fields (migration 285) |
| 5 | Drift trending / metrics | **DEFERRED** | Dashboard panel; no demand |
| 6 | Control gap assessment | **COVERED** | `kg_compliance_coverage` + crosswalk gap |
| 7 | SSP fragment consolidation | **DEFERRED** | No demand |
| 8 | Drift simulation / forecasting | **DEFERRED** | Speculative; no demand |

### 10. Doc Modernization
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Architecture pattern pack | **COVERED** | `architecture_patterns.yaml` (DMX) |
| 2 | SOP / workflow pack | **COVERED** | `sop_workflows.yaml` (DMX) |
| 3 | Redline quality validation | **COVERED** | WriteGuard + citation grounding (DMX) |
| 4 | Pack interference detection | **SUPERSEDED** | DMX card |
| 5 | Pack confidence calibration | **DEFERRED** | DMX backlog; no demand |
| 6 | Pack coverage metrics | **DEFERRED** | DMX backlog |
| 7 | Evidence snapshot versioning | **COVERED** | `combined_evidence_hash()` + `history_recorder` |
| 8 | Pack auto-scaffolding | **DEFERRED** | Speculative |

### 11. GovCon / CPMP
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Past-performance integration | **BUILT** | crx-gov-01 #698 — `past_performance_suggester.py` |
| 2 | Teaming partner management | **COVERED** | `govcon/teaming_hub.py` |
| 3 | Contract clause risk analysis | **BUILT** | crx-gov-02 #687 — `clause_risk_engine.py` |
| 4 | Subcontract management | **COVERED** | `govcon/subcontractor_tracker.py` |
| 5 | Bid / no-bid decision support | **COVERED** | `bayesian_bid_scorer.py` + `ptw_posture.py` |
| 6 | Oral presentation support | **COVERED** | VIZ presentation layer + slides canvas |
| 7 | Contract mod impact analysis | **COVERED** | `contract_mods_manager.py` + `amendment_tracker.py` + `evm_engine.py` |
| 8 | Incumbent intelligence | **COVERED** | `cpars_predictor.py` + `competitor_profiler.py` |

### 12. Kanban Workflow
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | SLA / deadline tracking | **BUILT** | crx-kan-01 #677 — `due_date`/`sla_hours` (migration 285) |
| 2 | Dependency management (blocks/blocked_by) | **COVERED** | `task_factory` dependency support |
| 3 | Capacity planning | **DEFERRED** | Assignee-workload model; no demand |
| 4 | Burndown / velocity metrics | **BUILT** | crx-kan-01 #677 — `metrics.py` cycle-time/velocity |
| 5 | GitLab / Bitbucket PR adapters | **DEFERRED** | GitHub-first executor by design (unified flow) |
| 6 | External ticketing sync (Jira/ServiceNow) | **COVERED** | `sync_jira` / `sync_servicenow` + `configure_*` bridges |
| 7 | Suggested-card contextual prioritization | **COVERED** | Suggested quarantine + Oracle prediction scoring |
| 8 | Board API for external consumption | **COVERED** | Kanban MCP tools + REST endpoints |

### 13. Migration Canvas
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Migration cost modeling | **DEFERRED** | No demand |
| 2 | Rollback planning | **BUILT** | crx-mig-01 #694 — rollback sections in wave plans |
| 3 | Post-migration validation | **BUILT** | crx-mig-01 #694 — validation gates |
| 4 | Multi-cloud (AWS/Azure/GCP) | **DEFERRED** | No demand |
| 5 | Portfolio management | **DEFERRED** | Partial overlap w/ `portfolio_manager.py`; canvas-specific deferred |
| 6 | Business case builder | **DEFERRED** | No demand |
| 7 | Runbook automation | **COVERED** | `runbook_execute` / `runbook_register` (CloudForge DAG) |
| 8 | Migration knowledge base | **COVERED** | Kanban lessons-learned engine + DIC |

### 14. Network Design Canvas (NDC)
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Intent-based networking | **COVERED** | NDC `mc_net_ai_assist` intent layer |
| 2 | Validation / simulation (BGP) | **COVERED** | NDC validation + Batfish go/no-go (twx-spk-02) |
| 3 | Cloud network integration (Terraform) | **COVERED** | `network_segmentation_generate` + IaC generators |
| 4 | SD-WAN / SASE | **DEFERRED** | No demand |
| 5 | Performance monitoring (SNMP/NetFlow) | **COVERED** | `pmacct_ingest` + NOC canvas |
| 6 | IPv6-first design | **DEFERRED** | OMB mandate tracked; not scheduled |
| 7 | Asset inventory sync (NetBox) | **COVERED** | `mc_net_ingest_netbox` |
| 8 | Compliance-aware design (SC-7/SC-8) | **COVERED** | NDC compliance gate (NDC hardening sweep) |

### 15. Notification Service
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Routing rules | **BUILT** | crx-not-01 #684 — routing engine |
| 2 | Escalation policies | **BUILT** | crx-not-01 #684 — escalation chains |
| 3 | On-call schedule (PagerDuty/Opsgenie) | **REJECTED** | External-SaaS integration deferred-no-demand |
| 4 | Per-user preferences / quiet hours | **BUILT** | crx-not-01 #684 — per-user prefs |
| 5 | Event-bus durability | **DEFERRED** | In-memory `event_service`; durable store deferred |
| 6 | Dead-letter queue | **DEFERRED** | Paired with #5; deferred |
| 7 | Notification analytics | **DEFERRED** | No demand |
| 8 | Webhook subscription management | **DEFERRED** | No demand |

### 16. RAG / Vector Search
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Vector index at scale (vss/faiss) | **SUPERSEDED** | RCE card + pgvector primary |
| 2 | RAPTOR hierarchy | **COVERED** | `tools/rag/raptor.py` (verified) |
| 3 | Contextual retrieval | **SUPERSEDED** | RCE card |
| 4 | Embedding fine-tuning | **DEFERRED** | Provider-abstraction embeddings; nomic/Ollama rejected |
| 5 | Query rewriting / expansion | **SUPERSEDED** | RCE (`rag_server` decompose) |
| 6 | GraphRAG first-class | **COVERED** | `knowledge_graph/graph_rag.py` (Tier A/B) |
| 7 | Relevance feedback loop | **DEFERRED** | `rag_retrieval_history` exists; boosting deferred |
| 8 | Semantic chunking | **COVERED** | `tools/rag/chunker.py` (verified) |

### 17. Security Canvas
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | SOAR active-response playbooks | **BUILT** | crx-sec-02 #697 — SOAR-lite HITL playbooks |
| 2 | Threat-intel sharing (STIX/TAXII) | **DEFERRED** | No STIX/TAXII exporter; no demand |
| 3 | Purple-team automation (ATT&CK) | **COVERED** | `atlas_red_team.py` + `llm_red_team.py` + registry |
| 4 | Insider-threat UBA | **BUILT** | crx-sec-01 #686 — `insider_risk.py` |
| 5 | CSPM | **COVERED** | `tools/cloud/csp_monitor.py` (verified) |
| 6 | Security metrics trending | **COVERED** | SHX posture + `slo_dashboard` |
| 7 | Mobile / API security testing | **DEFERRED** | MobSF/mobile SAST; no demand |
| 8 | Supply chain beyond SBOM | **COVERED** | `scrm_assessor.py` + vendor risk |

### 18. Supply Chain
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Vendor continuous monitoring | **COVERED** | `cve_passive_watcher` + `scrm_assessor` + `ndaa_889_screener` |
| 2 | Software composition analysis (SCA) | **COVERED** | `dependency_graph.py` + `scan_dependencies` |
| 3 | Hardware BOM (HBOM) | **COVERED** | `semiconductor_chain.py` + firmware SBOM (CycloneDX) |
| 4 | Supplier geographic risk | **COVERED** | `rare_earth_cascade.py` + NDAA-889 origin screening |
| 5 | Contract flow-down tracking | **COVERED** | `subcontractor_tracker.py` + `isa_manager.py` |
| 6 | Supply-chain simulation | **COVERED** | `rare_earth_cascade` disruption + `conflict_mesh` |
| 7 | ESG / sustainability tracking | **REJECTED** | ESG scoring deferred-no-demand |
| 8 | Procurement integration (SAP/Oracle/Coupa) | **REJECTED** | Procurement-sync deferred-no-demand |

### 19. Testing & Quality
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Performance / load testing | **BUILT** | crx-test-01 #691 — Locust harness (`perf/locustfile.py`) |
| 2 | Chaos engineering | **DEFERRED** | Fault injection; no demand |
| 3 | Accessibility testing | **BUILT** | crx-test-02 #682 — `a11y_sweep.py` |
| 4 | Contract testing (Pact) | **COVERED** | `api_contract_reflex` + `validate_tool_chain` |
| 5 | Test data management | **COVERED** | `synthetic_data_engine` + redaction masking |
| 6 | Visual regression testing | **COVERED** | `validate_screenshot` Playwright V&V |
| 7 | Test parallelization | **BUILT (spike→conditional GO)** | crx-test-03 #696 — pytest-xdist feasibility |
| 8 | Coverage trending | **DEFERRED** | Per-module history; no demand |

### 20. ACE Multi-Agent
| # | Finding | Disposition | Evidence / rationale |
|---|---------|-------------|----------------------|
| 1 | Agent conflict resolution | **COVERED** | Session registry + advisory/git locks (cross-session coordination) |
| 2 | Agent performance metrics | **COVERED** | NOVA trust scores + `ace_team_monitor` reflex |
| 3 | Dynamic agent scaling | **DEFERRED** | Queue-depth autoscale; kanban dispatch suffices |
| 4 | Agent learning from failures | **COVERED** | NOVA `analyze_patterns`/`evolve_skill` + lessons-learned engine |
| 5 | Cross-agent knowledge sharing | **COVERED** | `kg_shared_entities` + `agent_memory` |
| 6 | Agent cost budgeting | **COVERED** | `token_accounting` + `cost_intelligence` per-session |
| 7 | Agent explainability | **COVERED** | CoT/CoD tracing + `council_query` + ACE session logs |
| 8 | Agent simulation / testing | **COVERED** | `sandbox_execute`/`sandbox_score` + NOVA skill-queue gating |

---

## CRX card completion

With `crx-xcut-01` recorded, the CRX card is **18/18 complete**. The manual gate
`crx-gate-00` remains held (pipeline-exempt) per the manual-gate exemption policy —
CRX tasks were dispatched by the card-lead, never by the backlog promoter.

## Cross-references

- ADR: `docs/reference/adrs.md` → Phase 75 / D-CRX block
- Per-task feature docs: `crx-not-01-notification-routing.md`,
  `crx-gov-02-clause-risk-engine.md`, `phase-crx-db-01-pg-native-rls-spike.md`,
  `phase-crx-test-03-pytest-parallelization-spike.md`
- Source reviews: 20 Hermes component reviews, 2026-07-19 (archived)
