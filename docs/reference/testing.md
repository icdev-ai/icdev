# ICDEV™ Testing Reference

Testing framework, test commands, and validation pipeline. See [CLAUDE.md](../../CLAUDE.md) for behavioral instructions.

---

### Testing Framework (Adapted from ADW)
```bash
# ICDEV™ platform tests (D155 — 21 test files, ~330+ tests)
pytest tests/ -v --tb=short                          # Run all platform tests
pytest tests/test_circuit_breaker.py -v              # Circuit breaker tests
pytest tests/test_retry.py -v                        # Retry utility tests
pytest tests/test_correlation.py -v                  # Correlation ID tests
pytest tests/test_errors.py -v                       # Error hierarchy tests
pytest tests/test_migration_runner.py -v             # Migration runner tests
pytest tests/test_backup_manager.py -v               # Backup/restore tests
pytest tests/test_openapi_spec.py -v                 # OpenAPI spec tests
pytest tests/test_metrics.py -v                      # Prometheus metrics tests
pytest tests/test_rest_api.py -v                     # REST API endpoint tests
pytest tests/test_swagger_ui.py -v                   # Swagger UI tests
pytest tests/test_audit_logger.py -v                 # Audit logger tests
pytest tests/test_init_icdev_db.py -v                # DB init tests
pytest tests/test_platform_db.py -v                  # Platform DB tests
pytest tests/test_readiness_scorer.py -v             # Readiness scorer tests
pytest tests/test_dev_profile_manager.py -v          # Dev profile manager tests (33 tests)
pytest tests/test_manifest_loader.py -v              # Manifest loader tests (32 tests)
pytest tests/test_session_context_builder.py -v      # Session context builder tests (26 tests)
pytest tests/test_pipeline_config_generator.py -v    # Pipeline config generator tests (14 tests)
pytest tests/test_icdev_client.py -v                 # SDK client tests (12 tests)
pytest tests/test_tool_detector.py -v                # AI tool detector tests (10 tests)
pytest tests/test_instruction_generator.py -v        # Instruction generator tests (14 tests)
pytest tests/test_mcp_config_generator.py -v         # MCP config generator tests (8 tests)
pytest tests/test_skill_translator.py -v             # Skill translator tests (10 tests)
pytest tests/test_companion.py -v                    # Companion orchestrator tests (7 tests)
pytest tests/test_prompt_injection_detector.py -v    # Prompt injection detector tests (47 tests)
pytest tests/test_ai_telemetry.py -v                 # AI telemetry logger tests (12 tests)
pytest tests/test_cloud_providers.py -v              # Cloud provider abstraction tests (20 tests)
pytest tests/test_atlas_assessor.py -v               # ATLAS assessor tests (15 tests)
pytest tests/test_multi_cloud_llm.py -v              # Multi-cloud LLM provider tests (12 tests)
pytest tests/test_child_registry.py -v               # Child registry + telemetry tests (18 tests)
pytest tests/test_evolutionary_intelligence.py -v    # Genome, evaluation, staging, propagation tests (25 tests)
pytest tests/test_genome_evolution.py -v             # Absorption, learning, cross-pollination tests (20 tests)
pytest tests/test_atlas_red_team.py -v               # ATLAS red teaming scanner tests (10 tests)
pytest tests/test_ai_bom_generator.py -v             # AI BOM generator tests (14 tests)
pytest tests/test_phase36_phase37_integration.py -v  # Phase 36↔37 security integration tests (17 tests)
pytest tests/test_cloud_monitoring_iam.py -v         # Cloud monitoring/IAM/registry tests (15 tests)
pytest tests/test_ibm_providers.py -v                # IBM Cloud provider tests (44 tests)
pytest tests/test_region_validator.py -v             # CSP region validator tests (18 tests)
pytest tests/test_translation_manager.py -v          # Translation pipeline tests (35 tests)
pytest tests/test_dependency_mapper.py -v            # Dependency mapper tests (16 tests)
pytest tests/test_source_extractor.py -v             # Source extractor tests (22 tests)
pytest tests/test_behavioral_drift.py -v             # Behavioral drift detection tests (14 tests)
pytest tests/test_tool_chain_validator.py -v          # Tool chain validator tests (22 tests)
pytest tests/test_agent_output_validator.py -v        # Agent output validator tests (22 tests)
pytest tests/test_agent_trust_scorer.py -v            # Agent trust scorer tests (22 tests)
pytest tests/test_mcp_tool_authorizer.py -v           # MCP tool authorizer tests (28 tests)
pytest tests/test_behavioral_red_team.py -v           # Behavioral red teaming tests (13 tests)
pytest tests/test_owasp_agentic_assessor.py -v        # OWASP Agentic assessor tests (16 tests)
pytest tests/test_schemas.py -v                      # Shared schema enforcement tests (29 tests)
pytest tests/test_state_tracker.py -v                # Dirty-tracking state push tests (16 tests)
pytest tests/test_extension_manager.py -v            # Active extension hooks tests (18 tests)
pytest tests/test_chat_manager.py -v                 # Multi-stream chat + intervention tests (22 tests)
pytest tests/test_history_compressor.py -v           # 3-tier history compression tests (25 tests)
pytest tests/test_memory_consolidation.py -v         # AI-driven memory consolidation tests (22 tests)
pytest tests/test_context_server.py -v               # Semantic layer MCP tools tests (20 tests)
pytest tests/test_code_pattern_scanner.py -v         # Dangerous pattern detection tests (30 tests)
pytest tests/test_register_external_patterns.py -v   # Innovation signal registration tests (15 tests)
pytest tests/test_claude_dir_validator.py -v         # .claude directory governance validator tests (50 tests)
pytest tests/test_tracer.py -v                        # Tracer ABC + SQLiteTracer tests (43 tests)
pytest tests/test_trace_context.py -v                 # W3C traceparent + context propagation tests (30 tests)
pytest tests/test_mcp_instrumentation.py -v           # MCP auto-instrumentation tests (8 tests)
pytest tests/test_a2a_trace_propagation.py -v         # A2A distributed tracing tests (10 tests)
pytest tests/test_otel_tracer.py -v                   # OTelTracer + OTelSpan mock tests (17 tests)
pytest tests/test_prov_recorder.py -v                 # Provenance recorder tests (30 tests)
pytest tests/test_agent_shap.py -v                    # AgentSHAP Shapley value tests (20 tests)
pytest tests/test_xai_assessor.py -v                  # XAI compliance assessor tests (34 tests)
pytest tests/test_unified_server.py -v                 # Unified MCP gateway tests (42 tests)
pytest tests/test_oscal_tools.py -v                    # OSCAL ecosystem tools tests (40 tests)
pytest tests/test_omb_m25_21_assessor.py -v              # OMB M-25-21 assessor tests
pytest tests/test_omb_m26_04_assessor.py -v              # OMB M-26-04 assessor tests
pytest tests/test_nist_ai_600_1_assessor.py -v           # NIST AI 600-1 assessor tests
pytest tests/test_gao_ai_assessor.py -v                  # GAO AI assessor tests
pytest tests/test_model_card_generator.py -v             # Model card generator tests
pytest tests/test_ai_transparency.py -v                  # AI transparency integration tests
pytest tests/test_accountability_manager.py -v          # Accountability manager tests (25 tests)
pytest tests/test_ai_impact_assessor.py -v              # AI impact assessor tests (13 tests)
pytest tests/test_ai_incident_response.py -v            # AI incident response tests (19 tests)
pytest tests/test_ai_reassessment_scheduler.py -v       # AI reassessment scheduler tests (18 tests)
pytest tests/test_ai_accountability_audit.py -v         # AI accountability audit tests (20 tests)
pytest tests/test_assessor_accountability_fixes.py -v   # Assessor accountability fixes tests (24 tests)
pytest tests/test_ai_governance_intake.py -v            # AI governance intake detection tests (37 tests)
pytest tests/test_ai_governance_chat_extension.py -v    # AI governance chat extension tests (28 tests)
pytest tests/test_code_analyzer.py -v                   # Code analyzer AST self-analysis tests (29 tests)
pytest tests/test_runtime_feedback.py -v                # Runtime feedback collector tests (22 tests)
pytest tests/test_dispatcher_mode.py -v                 # Dispatcher-only orchestrator mode tests (47 tests)
pytest tests/test_prompt_chain_executor.py -v           # Declarative prompt chain executor tests (63 tests)
pytest tests/test_anvil_critique.py -v                  # ANVIL adversarial critique tests (36 tests)
pytest tests/test_session_purpose.py -v                 # Session purpose + async result injection + tiered file access tests (27 tests)
pytest tests/test_research_engine.py -v                 # Industry Research Engine tests (68 tests)
pytest tests/test_rag_vector_stores.py -v               # RAG vector store backend tests (40 tests)
pytest tests/test_rag_chunker.py -v                     # RAG adaptive chunking tests (20 tests)
pytest tests/test_rag_retriever.py -v                   # RAG retrieval pipeline tests (25 tests)
pytest tests/test_rag_reranker.py -v                    # RAG re-ranking tests (15 tests)
pytest tests/test_rag_ingestion.py -v                   # RAG ingestion manager tests (25 tests)
pytest tests/test_rag_retention.py -v                   # RAG tier migration tests (15 tests)
pytest tests/test_rag_two_tier.py -v                    # RAG two-tier LLM integration tests (10 tests)
pytest tests/test_rag_child_app.py -v                   # RAG child app integration tests (20 tests)
pytest tests/test_finetune_provider.py -v         # Fine-tune provider ABC tests (21 tests)
pytest tests/test_finetune_gpu_detector.py -v      # GPU detection tests (20 tests)
pytest tests/test_finetune_dataset.py -v           # Dataset management tests (32 tests)
pytest tests/test_finetune_training_engine.py -v   # Training engine tests (65 tests)
pytest tests/test_finetune_evaluator.py -v         # Evaluator + promotion tests (67 tests)
pytest tests/test_finetune_router_integration.py -v # Router integration tests (23 tests)
pytest tests/test_finetune_cloud_providers.py -v   # Cloud provider tests (74 tests)
pytest tests/test_api_surface_extractor.py -v            # API surface extractor tests (38 tests)
pytest tests/test_bayesian_teacher.py -v                 # Bayesian teaching intelligence tests (74 tests)
pytest tests/test_workflow_loop.py -v                    # Workflow discipline engine tests (240+ tests)
pytest tests/test_blueprint_verifier.py -v               # Blueprint verifier tests (38 tests)
pytest tests/test_credential_broker.py -v                # Credential broker tests (30 tests)
pytest tests/test_egress_monitor.py -v                   # Egress monitor tests (17 tests)
pytest tests/test_egress_policy_manager.py -v            # Egress policy manager tests (29 tests)
pytest tests/test_propagation_verifier.py -v             # Propagation verifier tests (12 tests)
pytest tests/test_sandbox_scorer.py -v                   # Sandbox scorer tests (15 tests)
pytest tests/test_autoresearch.py -v                     # Bayesian Autoresearch tests (33 tests)

# .claude directory governance
python tools/testing/claude_dir_validator.py --json   # Validate .claude config alignment (exit 0 = pass)
python tools/testing/claude_dir_validator.py --human   # Human-readable terminal output
python tools/testing/claude_dir_validator.py --check append-only --json  # Single check

# Health check
python tools/testing/health_check.py                 # Full system health check
python tools/testing/health_check.py --json           # JSON output

# Production readiness audit (38 checks, 7 categories)
python tools/testing/production_audit.py --human --stream              # Full audit with streaming
python tools/testing/production_audit.py --json                        # JSON output
python tools/testing/production_audit.py --category security --json    # Single category
python tools/testing/production_audit.py --category security,compliance --json  # Multiple categories
python tools/testing/production_audit.py --gate --json                 # Gate evaluation (exit code 0=pass, 1=fail)
pytest tests/test_production_audit.py -v             # Production audit tests (25 tests)

# Production remediation (auto-fix audit blockers)
python tools/testing/production_remediate.py --human --stream              # Auto-fix + stream
python tools/testing/production_remediate.py --auto --json                 # Auto-fix all (JSON)
python tools/testing/production_remediate.py --dry-run --human --stream    # Preview fixes
python tools/testing/production_remediate.py --check-id SEC-002 --auto     # Single check
python tools/testing/production_remediate.py --skip-audit --auto --json    # Reuse latest audit
pytest tests/test_production_remediate.py -v          # Remediation tests (25 tests)

# Test orchestrator (full pipeline: unit + BDD + E2E + gates)
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/test_orchestrator.py --project-dir /path --skip-e2e --project-id "proj-123"

# E2E tests (Playwright MCP)
python tools/testing/e2e_runner.py --discover         # List available E2E test specs
python tools/testing/e2e_runner.py --run-all           # Execute all E2E tests
python tools/testing/e2e_runner.py --test-file .claude/commands/e2e/dashboard_health.md
python tools/testing/e2e_runner.py --run-all --validate-screenshots    # E2E + vision validation
python tools/testing/e2e_runner.py --run-all --validate-screenshots --vision-strict  # Vision failures = test failures

# Screenshot validation (vision LLM — Ollama LLaVA / Claude / GPT-4o)
python tools/testing/screenshot_validator.py --check --json                           # Check vision model availability
python tools/testing/screenshot_validator.py --image screenshot.png --assert "CUI banner is visible" --json
python tools/testing/screenshot_validator.py --batch-dir .tmp/test_runs/screenshots/ --json
```

**Testing Architecture (9-step pipeline, adapted from ADW test.md):**
1. **py_compile** — Python syntax validation (catches missing colons, bad indentation before tests run)
2. **Ruff** (`ruff>=0.12`) — Ultra-fast Python linter (replaces flake8+isort+black, written in Rust)
3. **pytest** (tests/) — Unit/integration tests with coverage
4. **behave/Gherkin** (features/) — BDD scenario tests for business requirements
5. **Bandit** — SAST security scan (SQL injection, XSS, hardcoded secrets)
6. **Playwright MCP** (.claude/commands/e2e/*.md) — Browser automation E2E tests
7. **Vision validation** (optional) — LLM-based screenshot analysis (CUI banners, error detection, content verification)
8. **Acceptance validation** (V&V) — Deterministic acceptance criteria verification: maps plan criteria to test evidence, checks rendered pages for error patterns (per `acceptance_validation` gate in `security_gates.yaml`)
9. **Security + Compliance gates** — CUI markings, STIG (0 CAT1), secret detection

**Claude Code test commands** (in .claude/commands/):
- `/test` — Full application validation suite (syntax + quality + unit + BDD + security)
- `/test_e2e` — Execute E2E test via Playwright MCP with screenshots + CUI verification
- `/resolve_failed_test` — Fix a specific failing test (minimal, targeted fix)
- `/resolve_failed_e2e_test` — Fix a specific failing E2E test

**Key patterns from ADW:** parse_json (markdown-wrapped JSON), Pydantic data types (TestResult, E2ETestResult), dual logging (file+console), safe subprocess env, retry with resolution (max 4 unit / max 2 E2E), fail-fast E2E, stdin=DEVNULL for Claude Code subprocesses
