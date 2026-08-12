# ICDEV™ Compliance, Security & Configuration Reference

Compliance frameworks, security gates, and configuration files. See [CLAUDE.md](../../CLAUDE.md) for behavioral instructions.

---

## Compliance Frameworks Supported

| Framework | Catalog | Assessor | Report |
|-----------|---------|----------|--------|
| NIST 800-53 Rev 5 | `nist_800_53.json` | `control_mapper.py` | SSP, control matrix |
| FedRAMP Moderate | `fedramp_moderate_baseline.json` | `fedramp_assessor.py` | `fedramp_report_generator.py` |
| FedRAMP High | `fedramp_high_baseline.json` | `fedramp_assessor.py` | `fedramp_report_generator.py` |
| NIST 800-171 | `nist_800_171_controls.json` | via crosswalk | via crosswalk coverage |
| CMMC Level 2/3 | `cmmc_practices.json` | `cmmc_assessor.py` | `cmmc_report_generator.py` |
| DoD CSSP (DI 8530.01) | `dod_cssp_8530.json` | `cssp_assessor.py` | `cssp_report_generator.py` |
| CISA Secure by Design | `cisa_sbd_requirements.json` | `sbd_assessor.py` | `sbd_report_generator.py` |
| IEEE 1012 IV&V | `ivv_requirements.json` | `ivv_assessor.py` | `ivv_report_generator.py` |
| DoDI 5000.87 DES | `des_requirements.json` | `des_assessor.py` | `des_report_generator.py` |
| FIPS 199 | `nist_sp_800_60_types.json` | `fips199_categorizer.py` | Categorization report |
| FIPS 200 | `fips_200_areas.json` | `fips200_validator.py` | Gap report |
| CNSSI 1253 | `cnssi_1253_overlay.json` | via fips199_categorizer | Overlay application |
| CJIS Security Policy | `cjis_security_policy.json` | `cjis_assessor.py` | via base_assessor |
| HIPAA Security Rule | `hipaa_security_rule.json` | `hipaa_assessor.py` | via base_assessor |
| HITRUST CSF v11 | `hitrust_csf_v11.json` | `hitrust_assessor.py` | via base_assessor |
| SOC 2 Type II | `soc2_trust_criteria.json` | `soc2_assessor.py` | via base_assessor |
| PCI DSS v4.0 | `pci_dss_v4.json` | `pci_dss_assessor.py` | via base_assessor |
| ISO/IEC 27001:2022 | `iso27001_2022_controls.json` | `iso27001_assessor.py` | via base_assessor |
| NIST SP 800-207 (ZTA) | `nist_800_207_zta.json` | `nist_800_207_assessor.py` | via base_assessor |
| DoD MOSA (10 U.S.C. §4401) | `mosa_framework.json` | `mosa_assessor.py` | via base_assessor |
| MITRE ATLAS v5.4.0 | `atlas_mitigations.json` | `atlas_assessor.py` | `atlas_report_generator.py` |
| OWASP LLM Top 10 | `owasp_llm_top10.json` | `owasp_llm_assessor.py` | via base_assessor |
| NIST AI RMF 1.0 | `nist_ai_rmf.json` | `nist_ai_rmf_assessor.py` | via base_assessor |
| ISO/IEC 42001:2023 | `iso42001_controls.json` | `iso42001_assessor.py` | via base_assessor |
| SAFE-AI (NIST 800-53 AI) | `safeai_controls.json` | via crosswalk | AI-affected control overlay |
| OWASP Agentic AI | `owasp_agentic_threats.json` | `owasp_agentic_assessor.py` | via base_assessor |
| OWASP ASI01-ASI10 | `owasp_agentic_asi.json` | `owasp_asi_assessor.py` | via base_assessor |
| EU AI Act (Annex III) | `eu_ai_act_annex_iii.json` | `eu_ai_act_classifier.py` | via base_assessor (ISO 27001 bridge) |
| XAI (Observability) | `xai_requirements.json` | `xai_assessor.py` | via base_assessor |
| OMB M-25-21 (High-Impact AI) | `omb_m25_21_high_impact_ai.json` | `omb_m25_21_assessor.py` | via base_assessor |
| OMB M-26-04 (Unbiased AI) | `omb_m26_04_unbiased_ai.json` | `omb_m26_04_assessor.py` | via base_assessor |
| NIST AI 600-1 (GenAI Profile) | `nist_ai_600_1_genai.json` | `nist_ai_600_1_assessor.py` | via base_assessor |
| GAO-21-519SP (AI Accountability) | `gao_ai_accountability.json` | `gao_ai_assessor.py` | via base_assessor |

---

## Control Crosswalk

The crosswalk engine (`tools/compliance/crosswalk_engine.py`) uses a dual-hub model (ADR D111):
- **US Hub**: NIST 800-53 Rev 5 — domestic frameworks map directly (FedRAMP, CMMC, CJIS, HIPAA, etc.)
- **International Hub**: ISO/IEC 27001:2022 — international frameworks map via bridge
- **Bridge**: `iso27001_nist_bridge.json` connects the two hubs bidirectionally

Implementing AC-2 satisfies FedRAMP AC-2, 800-171 3.1.1, CMMC AC.L2-3.1.1, and cascades to CJIS/HIPAA/SOC 2/PCI DSS/ISO 27001/NIST 800-207 via the crosswalk engine.

---

## Security Gates (Blocking Conditions)

- **Code Review Gate:** ≥1 approval, all comments resolved, SAST clean, no secrets, CUI markings present
- **Merge Gate:** All tests pass, ≥80% coverage, 0 CAT1 STIG, 0 critical vulns, SBOM current
- **Deploy Gate:** Staging tests pass, compliance artifacts current, change request approved, rollback plan exists
- **FedRAMP Gate:** 0 other_than_satisfied on high-priority controls, encryption FIPS 140-2 required
- **CMMC Gate:** 0 not_met Level 2 practices, evidence current within 90 days
- **cATO Gate:** 0 expired evidence on critical controls, readiness ≥50%
- **DES Gate:** 0 non_compliant on critical DoDI 5000.87 Digital Engineering requirements
- **Migration Gate:** ATO coverage ≥95% maintained during modernization, compliance bridge validated per PI
- **RICOAS Gate:** Readiness score ≥0.7, 0 unresolved critical gaps, RED requirements must have alternative COAs
- **Supply Chain Gate:** 0 critical SCRM risks unmitigated, 0 expired ISAs with active data flows, 0 overdue critical CVE SLAs, 0 Section 889 prohibited vendors
- **FIPS 199 Gate:** Categorization required for ATO projects, IL6 must have CNSSI 1253, categorization approved
- **FIPS 200 Gate:** 0 not_satisfied requirement areas, all 17 areas assessed, coverage ≥80%
- **Marketplace Publish Gate:** 0 critical/high SAST findings, 0 secrets, 0 critical/high dep vulns, CUI markings present, SBOM generated, digitally signed
- **Marketplace Cross-Tenant Gate:** All publish gate requirements + human ISSO/security officer review completed + code review confirmed
- **Multi-Regime Gate:** All applicable frameworks must pass individual gates; overall pass requires 0 framework failures across all detected regimes
- **HIPAA Gate:** 0 not_satisfied on Administrative/Technical Safeguards, encryption FIPS 140-2 required for PHI
- **PCI DSS Gate:** 0 not_satisfied on Requirements 3-4 (data protection), 6 (secure development), 10 (logging)
- **CJIS Gate:** 0 not_satisfied on Policy Areas 4 (audit), 5 (access control), 6 (identification), 10 (encryption)
- **DevSecOps Gate:** 0 critical policy-as-code violations, 0 missing image attestations (when active), 0 unresolved critical SAST findings, 0 detected secrets
- **ZTA Gate:** ZTA maturity ≥ Advanced (0.34) for IL4+, mTLS enforced when service mesh active, default-deny NetworkPolicy required, no pillar at 0.0
- **MOSA Gate:** 0 external interfaces without ICD, 0 circular dependencies, modularity score ≥ 0.6, 0 direct coupling violations; warn on interface coverage < 80%, TSP expired/missing
- **Acceptance Validation Gate:** 0 failed acceptance criteria, 0 pages with error patterns (500, tracebacks, JS errors), plan must contain `## Acceptance Criteria` section
- **Remote Command Gate:** User binding required, signature verification on webhooks, replay window 300s, rate limit 30/user/min + 100/channel/min, icdev-deploy + icdev-init blocked on all remote channels, icdev-test/icdev-secure/icdev-build require confirmation
- **AI Security Gate:** Prompt injection defense active, AI telemetry enabled, AI BOM present, ≥80% ATLAS coverage, agent permissions configured
- **Genome Propagation Gate:** 72-hour stability window passed, capability evaluation score ≥0.65, HITL approval required for execution, compliance preservation verified in staging
- **Marketplace Prompt Injection Gate (Gate 8):** 0 high-confidence prompt injection patterns in asset files — blocking gate
- **Marketplace Behavioral Sandbox Gate (Gate 9):** 0 critical dangerous code patterns (eval, exec, os.system) — warning gate
- **Translation Gate:** Blocking: syntax errors in output, API surface < 90%, compliance coverage < 95%, secrets detected, CUI markings missing. Warning: round-trip similarity < 80%, type coverage < 85%, complexity increase > 30%, unmapped deps, stub functions, lint issues
- **Claude Config Alignment Gate:** Blocking: append-only table unprotected in pre_tool_use.py, hook syntax error, hook reference missing. Warning: dashboard route undocumented, E2E coverage gap, settings deny rule missing
- **AI Accountability Gate:** CAIO designated for high-impact AI, oversight plan exists, 0 unresolved critical AI incidents, no reassessments overdue >90 days; warn on appeal process not defined, ethics review not conducted, impact assessment missing, fairness gate not passing
- **AI Governance Gate:** CAIO designated for rights-impacting AI, oversight plan for high-impact AI, impact assessment completed; warn on model card missing, fairness assessment stale, reassessment overdue, AI inventory incomplete
- **Code Quality Gate:** Avg cyclomatic complexity ≤ 25 (blocking), maintainability score not declining, smell density ≤ 20/KLOC, dead code ≤ 10%
- **RAG Gate:** Provenance required per retrieval (blocking), cross-tenant query isolation enforced (blocking), content tracing in CUI requires approval (blocking); warn on ingestion staleness >7 days, low relevance trend, vector store unavailable
- **Fine-Tuning Gate:** CUI boundary violation blocks cloud training (blocking), cloud exceeds classification (blocking), unsigned LoRA for marketplace (blocking), provenance missing (blocking); warn on dataset < 20 examples, eval below baseline, GPU VRAM insufficient, auto-retrain disabled
- **Coherence Gate:** Schema-code mismatch (blocking), fixture-schema mismatch (blocking), append-only table unprotected (blocking); warn on config-code drift, signature-call risk, manifest incomplete, unused imports. Auto-fix available for imports and append-only via `--fix` flag. Runs in: workflow UNIFY phase, Genesis audit reflex, GKP promotion, CI/CD pipeline, marketplace publish, test orchestrator, production audit, heartbeat daemon

---

## Innovation Security Gates

| Gate | Condition |
|------|-----------|
| License Check | No GPL/AGPL/SSPL (copyleft risk) |
| Boundary Impact | RED items blocked from auto-generation |
| Compliance Alignment | Must not weaken existing compliance posture |
| FORGE Fit | Must map to Goal/Tool/Arg/Context/HardPrompt |
| Duplicate Detection | Content hash dedup (similarity > 0.85) |
| Budget Cap | Max 10 auto-solutions per PI |
| Build Gates | All existing security gates (SAST, deps, secrets, CUI) |
| Marketplace Publish | 7-gate marketplace pipeline |

---

## Args Configuration Files

| File | Purpose |
|------|---------|
| `args/project_defaults.yaml` | TDD settings, compliance baseline, security thresholds, infra defaults, CI/CD stages, monitoring, agent config |
| `args/agent_config.yaml` | 15 agent definitions with ports, TLS certs, Bedrock model config |
| `args/cui_markings.yaml` | CUI banner templates, designation indicators, portion marking rules |
| `args/security_gates.yaml` | Gate thresholds for code review, merge, deployment, FedRAMP, CMMC, cATO, RICOAS, supply chain (CAT1/CAT2, critical/high vulns) |
| `args/monitoring_config.yaml` | ELK/Splunk/Prometheus/Grafana endpoints, self-healing thresholds, SLA targets |
| `args/ricoas_config.yaml` | RICOAS settings: readiness weights/thresholds, gap detection, cost models, supply chain SLAs, integration mappings |
| `args/observability_config.yaml` | Hook settings, HMAC signing, SIEM forwarding, agent executor defaults, retention |
| `args/nlq_config.yaml` | NLQ-to-SQL settings: Bedrock model, row limits, blocked SQL patterns, SSE heartbeat |
| `args/worktree_config.yaml` | Git worktree settings: sparse checkout, cleanup policy, GitLab polling, tag-to-workflow mapping |
| `args/bedrock_models.yaml` | Bedrock model registry: model IDs, capabilities, pricing, fallback chain, probe interval, per-agent effort defaults |
| `args/agent_authority.yaml` | Domain authority matrix: Security (hard veto on code/deps/infra), Compliance (hard veto on artifacts/deploy), Architect (soft veto on design) |
| `args/marketplace_config.yaml` | Marketplace settings: scan gates, approval policies, federation sync, search weights, IL compatibility, community ratings |
| `args/classification_config.yaml` | Universal data classification: 10 data categories (CUI, PHI, PCI, CJIS, etc.), composite rules, banner templates, sensitivity order |
| `args/framework_registry.yaml` | All compliance frameworks: 20 active + planned, dual-hub model, data category triggers, bridge references |
| `args/mosa_config.yaml` | DoD MOSA settings: auto-trigger rules (DoD/IC + IL4+), modularity scoring weights, thresholds, ICD/TSP config, cATO integration flag, code enforcement, intake detection |
| `args/devsecops_config.yaml` | DevSecOps profile schema: 10 stages, 5 maturity levels, tool selections, intake detection keywords |
| `args/zta_config.yaml` | ZTA 7-pillar maturity model (DoD ZTA Strategy), service mesh options, policy engines, PDP references, posture scoring |
| `args/cli_config.yaml` | Optional CLI capabilities: 4 independent toggles (CI/CD automation, parallel agents, container execution, scripted intake), tenant ceiling, cost controls, environment detection |
| `args/remote_gateway_config.yaml` | Remote Command Gateway: environment mode (connected/air_gapped), 5 channel definitions (telegram, slack, teams, mattermost, internal_chat), security settings (binding TTL, signatures, rate limits), command allowlist with per-channel restrictions |
| `args/scaling_config.yaml` | Auto-scaling: HPA profiles (core/domain/support/dashboard/api_gateway), PDB config, topology spread, node autoscaler type (cluster-autoscaler/karpenter/none), custom metrics (Phase 2), rate limiter backend (in_memory/redis) |
| `args/resilience_config.yaml` | Circuit breaker defaults + per-service overrides (bedrock, redis, jira, servicenow, gitlab); retry defaults (max_retries, base_delay, max_delay) |
| `args/db_config.yaml` | Database migration settings (auto_migrate, checksum validation, lock timeout); backup settings (retention, encryption, per-database schedules); tenant backup policies |
| `args/spec_config.yaml` | Spec-kit pattern configuration (D156-D161): quality checklist, constitution, clarification (max questions, impact/uncertainty levels), spec directory structure, parallel markers |
| `args/skill_injection_config.yaml` | Selective skill injection (D167): 9 category definitions with keywords→commands/goals/context_dirs, file_extension_map, path_pattern_map, always_include, confidence_threshold |
| `args/memory_config.yaml` | Time-decay memory ranking (D168): per-type half-lives (fact=90d, preference=180d, event=7d, insight=30d, task=14d, relationship=120d), scoring weights (relevance=0.60, recency=0.25, importance=0.15), importance resistance threshold |
| `args/dev_profile_config.yaml` | Dev profile dimensions (D184): 10 dimension categories (language, style, testing, architecture, security, compliance, operations, documentation, git, ai), cascade rules, detection keywords, intake signals, task-dimension mapping |
| `args/companion_registry.yaml` | Universal AI Companion (D194): 10 tool definitions (Claude Code, Codex, Gemini, Copilot, Cursor, Windsurf, Amazon Q, JetBrains/Junie, Cline, Aider), instruction file paths, MCP support flags, skill formats, env detection signals, capabilities |
| `args/innovation_config.yaml` | Innovation Engine (D199): web sources (GitHub, NVD, SO, HN, package registries, compliance feeds), signal categories, 5-dimension scoring weights/thresholds, 5-stage triage rules, solution generation config, introspective analysis, competitive intel, standards monitoring, feedback calibration, scheduling (daemon mode, quiet hours) |
| `args/cloud_config.yaml` | Cloud-Agnostic Architecture (D225, D232): CSP selection (aws/azure/gcp/oci/ibm/local), cloud_mode (commercial/government/on_prem/air_gapped), region, impact level, per-CSP settings (GovCloud, Azure Government, Assured Workloads, OCI Government, IBM IC4G), per-service CSP overrides for secrets/storage/kms, region certification validation (D234) |
| `args/csp_monitor_config.yaml` | CSP Service Monitor (D239): 5 CSP sources (AWS, Azure, GCP, OCI, IBM) with RSS/API/HTML endpoints, signal generation (8 change types with category/score/urgency mapping), diff engine, notification/escalation, changelog generation, Innovation Engine integration, scheduling (daemon mode, quiet hours) |
| `args/security_gates.yaml` | (updated) Added `atlas_ai` gate with blocking conditions: critical_atlas_technique_unmitigated, prompt_injection_defense_inactive, ai_telemetry_not_active, agent_permissions_not_configured, ai_bom_missing; thresholds: min_atlas_coverage_pct=80, ai_telemetry_required=true |
| `args/translation_config.yaml` | Cross-Language Translation (D242-D256): 30 language pairs, extraction parsers, translation settings (max_chunk_lines=500, temperature=0.2, candidates=3), repair (max_attempts=3, compiler_feedback), type_checking, assembly (per-language project conventions), validation thresholds (api_surface>=0.90, type_coverage>=0.85, round_trip>=0.80, complexity<=30%), test translation framework mappings, compliance (95% control coverage) |
| `args/extension_config.yaml` | Active Extension Hooks (D261-D264): hook point configs (10 extension points), layered override rules (project > tenant > default), safety limits (max 30s total handler time, exception isolation), behavioral vs observational tiers |
| `args/context_config.yaml` | Semantic Layer MCP Tools (D277): CLAUDE.md indexing, cache TTL, agent-role→section mapping (10 agent roles), section refresh on mtime change |
| `args/code_pattern_config.yaml` | Dangerous Pattern Detection (D278): per-language patterns (Python, Java, Go, Rust, C#, TypeScript + universal), scan settings (skip_dirs, file_extensions, max_file_size), severity classification (critical/high/medium/low) |
| `args/security_gates.yaml` | (updated) Added `code_patterns` gate with max_critical=0, max_high=0, max_medium=10 |
| `args/owasp_agentic_config.yaml` | OWASP Agentic AI Security (Phase 45): behavioral drift thresholds (z-score, 7-day baseline), tool chain rules (4 default: secrets→external, read→exfil, privesc→deploy, rapid burst), output validation (classification leak, SSN, credentials, private keys), trust scoring (decay/recovery factors, 3 trust levels), MCP per-tool RBAC (`enabled` + `default_policy` only — the per-tool `min_il` / `required_roles` declarations live in `tools/mcp/tool_registry.py` since exa-policy-07; 5 roles: admin, pm, developer, isso, co) |
| `args/security_gates.yaml` | (updated) Added `owasp_agentic` gate with blocking: agent_trust_below_untrusted, tool_chain_critical_violation, output_classification_leak, behavioral_drift_critical, mcp_authorization_not_configured; thresholds: min_trust_score=0.30, max_critical_chain_violations=0, max_critical_output_violations=0 |
| `args/observability_tracing_config.yaml` | Observability & XAI (Phase 46, D290): dual-mode tracer config (otel/sqlite auto-detect via ICDEV_MLFLOW_TRACKING_URI), sampling rate, retention (sqlite_retention_days, mlflow_retention_days), content tracing policy (hash-only vs plaintext, ICDEV_CONTENT_TRACING_ENABLED), PROV-AGENT settings, AgentSHAP defaults (iterations, seed), XAI assessment thresholds |
| `args/security_gates.yaml` | (updated) Added `observability_xai` gate with blocking: tracing_not_active, provenance_graph_empty, xai_assessment_not_completed, content_tracing_active_in_cui_without_approval; thresholds: tracing_required=true, provenance_required=true, shap_max_age_days=30, min_xai_coverage_pct=80 |
| `args/oscal_tools_config.yaml` | OSCAL Ecosystem Tools (D302-D306): oscal-cli paths/timeout/JVM args, oscal-pydantic validation toggles, catalog source priority (official NIST → ICDEV™ fallback), validation pipeline order (structural → pydantic → Metaschema), max errors per validator |
| `args/security_gates.yaml` | (updated) Added `ai_transparency` gate with blocking: high_impact_ai_not_classified, model_cards_missing_for_deployed_models, ai_inventory_incomplete, gao_evidence_gaps_on_critical_practices, confabulation_detection_not_active; thresholds: min_gao_evidence_coverage_pct=80 |
| `args/ai_governance_config.yaml` | AI Governance Integration (Phase 50, D322-D330): intake detection keywords by 6 pillars, auto-trigger rules (federal agencies, impact level), chat governance (advisory cooldown, AI keyword list, priority order), readiness dimension component weights, probe questions for missing pillars |
| `args/security_gates.yaml` | (updated) Added `ai_governance` gate with blocking: caio_not_designated_for_rights_impacting_ai, oversight_plan_missing_for_high_impact_ai, impact_assessment_not_completed; warning: model_card_missing, fairness_assessment_stale, reassessment_overdue, ai_inventory_incomplete; thresholds: caio_required_for_rights_impacting=true, oversight_plan_required=true, impact_assessment_required=true |
| `args/code_quality_config.yaml` | Code Intelligence (Phase 52, D331-D337): smell thresholds (long_function, deep_nesting, high_complexity, too_many_params, god_class), maintainability weights (complexity 0.30, smell_density 0.20, test_health 0.20, coupling 0.15, coverage 0.15), audit thresholds, scan exclusion dirs, Innovation Engine integration |
| `args/security_gates.yaml` | (updated) Added `code_quality` gate with blocking: avg_cyclomatic_complexity_exceeds_critical; warning: maintainability_score_declining, high_smell_density, dead_code_exceeds_threshold; thresholds: max_avg_complexity=25, min_maintainability_score=0.40, max_smell_density_per_kloc=20, max_dead_code_pct=10 |
| `args/creative_config.yaml` | Creative Engine (Phase 58, D351-D360): domain, sources (review_sites, community_forums, github_issues, producthunt), competitor_discovery (refresh interval, auto_confirm=false), extraction (negative/feature-request keywords, 15 categories, clustering), scoring weights (pain_frequency 0.40, gap_uniqueness 0.35, effort_to_impact 0.25), thresholds (auto_spec 0.75, suggest 0.50), spec_generation, innovation_bridge (min_score 0.60), trends, scheduling (daemon interval, quiet hours) |
| `args/prompt_chains.yaml` | Declarative prompt chain templates (Phase 61, D-PC-1): 4 pre-built chains (plan_critique_refine, scout_analyze_recommend, security_review_chain, build_review_iterate), $INPUT/$ORIGINAL/$STEP{id} variable substitution, per-step agent routing, defaults (timeout 120s, model_effort high) |
| `args/anvil_critique_config.yaml` | ANVIL adversarial critique (Phase 61): 3 critics (security, compliance, knowledge) with focus areas and prompt context, consensus rules (GO: 0 critical + 0 high, CONDITIONAL: 0 critical, NOGO: any critical), revision prompt template, enabled toggle |
| `args/file_access_tiers.yaml` | Tiered file access control (Phase 61, D-ORCH-8): 3 tiers — zero_access (secrets, .env, .pem, .tfstate), read_only (compliance catalogs, lockfiles, build outputs), no_delete (CLAUDE.md, goals/, .git/, Dockerfiles). Glob-style patterns with ! exclusion |
| `args/research_config.yaml` | Industry Research Engine (Phase 63, D-RES-1 through D-RES-21): 9-stage pipeline, 9 source streams (community_forum, review_site, academic_paper, regulatory_body, open_source, saas_commercial, news_blog, patent, video), 6-dimension scoring weights/thresholds, regulatory mapping, capability mapping, build/buy analysis, dossier template, forecast generation (LLM/deterministic, cross-engine, surprise scoring), innovation/creative bridge, trend detection, air-gapped mode, scheduling |
| `args/rag_config.yaml` | Universal RAG Subsystem (Phase 64, D-RAG-1 through D-RAG-14): vector store backend (auto/sqlite/chromadb/faiss), embedding (768-dim nomic-embed-text, batch_size 20), chunking (short_threshold 500, chunk_size 2000, overlap 10%), retrieval (vector_top_k 50, final_top_k 5, bm25_boost 0.3, time_decay), rerank (qwen3-local, max_preview 400 chars), injection (max 4000 chars, system prompt, function denylist), ingestion (realtime + batch sources, dedup), retention (hot 30d/warm 365d float16/cold archive), provenance (hash-only queries, D282), child app (parent cache TTL 1h) |
| `args/finetune_config.yaml` | Fine-Tuning (Phase 64 Extension, D-FT-1 through D-FT-22): local engine (Unsloth, base models, quantization, distributed), GPU (min/preferred VRAM, CPU fallback), LoRA (rank/alpha/target_modules/dropout), training (LR, epochs, batch, scheduler), hyperparameter search (grid/random, max trials), dataset (min examples, auto-generate pairs), evaluation (auto-eval, BLEU/ROUGE-L/perplexity, LLM judge), promotion (auto-promote thresholds), retrain (threshold 50, cooldown 24h), cloud (OpenAI/Bedrock/Azure), export (GGUF Q4_K_M, Ollama prefix), marketplace (model card, SBOM, provenance), child app (copy adapters, inherit active models), provenance (PROV-AGENT chain) |
| `args/security_gates.yaml` | (updated) Added `rag` gate with blocking: rag_injection_without_provenance, rag_cross_tenant_query_detected, rag_content_tracing_in_cui_without_approval; warning: rag_ingestion_stale_over_7_days, rag_retrieval_low_relevance_trend, rag_vector_store_unavailable; thresholds: provenance_required=true, tenant_isolation_required=true, max_ingestion_staleness_days=7 |
| `args/filesync_config.yaml` | File Sync settings: detection (SHA-256, fast-skip mtime+size), watcher (optional watchdog, periodic scan), transfer (ThreadPoolExecutor, bandwidth throttle), SFTP, provider config, scheduling |
| `args/verify_loop_config.yaml` | Compiler-in-the-Loop Verification (LeanStral-adapted, D-VL-1): per-language verifier stacks (6 languages), loop settings (max 3 iterations, timeout), LLM repair config (system prompt, temperature, max chars), air-gap settings (prefer_local, local_repair_model), gate thresholds, audit (append-only) |
| `args/bayesian_teaching_config.yaml` | Bayesian Teaching Intelligence (D-BT-1): scoring weights (posterior_shift 0.35, discriminability 0.25, diversity 0.20, complexity_match 0.20), compliance ordering cascade probabilities, pair scoring, SmartEncoding dictionary, audit settings |
| `args/credential_broker_config.yaml` | Credential Broker (D-NC-1): token TTL/hash algorithm, function-to-provider mapping, trust revocation threshold, enable toggle (default: disabled for backward compat) |
| `args/evolution_config.yaml` | Evolution Daemon (D-EVO-1): master enable/disable, trust kernel risk tiers (GREEN/YELLOW/ORANGE), circuit breaker, 7 reflex schedules, capability evaluation 8-dimension weights, scanner-tier LLM config, outcome verification |
| `args/autoresearch_config.yaml` | Bayesian Autoresearch (D-AR-1 through D-AR-10): master enable/disable, Bayesian scoring weights (posterior_shift 0.30, discriminability 0.25, diversity 0.25, complexity_match 0.20), Thompson Sampling, cosine dedup threshold (0.85), experiment defaults (time_budget 300s, keep_threshold 0.005), circuit breaker (3 failures), Genesis reflex config (ORANGE tier, nightly 01:00), 6 domain programs in args/experiment_programs/ |
| `args/workflow_loop_config.yaml` | Workflow Discipline Engine (D-WF-1): loop lifecycle (max_tasks 5, abandon_after 72h), reconciliation settings, next action priority weights (staleness 0.30, compliance_gap 0.25, security_risk 0.20, loop_state 0.15, handoff_age 0.10), handoff TTL |
| `args/security_gates.yaml` | (updated) Added `evolution_lifecycle` gate with blocking: capability_absorbed_without_hitl, staging_test_failed_before_propagation, stability_window_not_elapsed, evaluation_score_below_minimum; thresholds: require_hitl_for_absorb=true, min_evaluation_score=0.65, stability_window_hours=72 |

---

## Self-Healing System

- **Confidence >= 0.7** + auto_healable → auto-remediate
- **Confidence 0.3-0.7** → suggest fix, require human approval
- **Confidence < 0.3** → escalate with full context
- Max 5 auto-heals/hour, 10-minute cooldown between same-pattern heals
