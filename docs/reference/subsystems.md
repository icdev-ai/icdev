# ICDEV™ Subsystems Reference

Major subsystem details for Innovation, Creative, Research engines; RICOAS; SaaS; Marketplace; CI/CD. See [CLAUDE.md](../../CLAUDE.md) for behavioral instructions.

---

## Innovation Engine — Autonomous Self-Improvement (Phase 35)

### Overview
ICDEV™ continuously improves itself by discovering developer pain points, CVEs, compliance changes, and competitive gaps from the web and internal telemetry — then generating solutions through the existing ANVIL build pipeline with full compliance triage.

### Pipeline
```
DISCOVER (web + introspective + competitive + standards)
    → SCORE (5-dimension weighted average)
        → TRIAGE (5-stage compliance gate)
            → GENERATE (template-based spec)
                → BUILD (ANVIL/M-ANVIL TDD)
                    → PUBLISH (marketplace 7-gate)
                        → MEASURE → CALIBRATE
```

### Commands
```bash
# Full pipeline (one-shot)
python tools/innovation/innovation_manager.py --run --json

# Individual stages
python tools/innovation/web_scanner.py --scan --all --json
python tools/innovation/signal_ranker.py --score-all --json
python tools/innovation/triage_engine.py --triage-all --json
python tools/innovation/trend_detector.py --detect --json
python tools/innovation/solution_generator.py --generate-all --json

# Introspective analysis (air-gap safe)
python tools/innovation/introspective_analyzer.py --analyze --all --json

# Competitive intelligence
python tools/innovation/competitive_intel.py --scan --all --json
python tools/innovation/competitive_intel.py --gap-analysis --json

# Standards body monitoring
python tools/innovation/standards_monitor.py --check --all --json

# Status and reporting
python tools/innovation/innovation_manager.py --status --json
python tools/innovation/innovation_manager.py --pipeline-report --json

# Continuous daemon mode
python tools/innovation/innovation_manager.py --daemon --json

# Feedback calibration
python tools/innovation/signal_ranker.py --calibrate --json
```

### Architecture Decisions
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
- **D239:** CSP monitoring integrated as Innovation Engine source (Phase 35) — reuses existing signal scoring, triage, and solution generation pipeline; CSP changes treated as innovation signals with category mapping and government/compliance boosts
- **D240:** Declarative CSP service registry as JSON catalog (extends D26 pattern) — baseline of all CSP services, compliance programs, regions, and FIPS status; monitor diffs live data against registry to detect changes; human review required before registry updates
- **D241:** CSP changelog generates actionable recommendations per change type — each change type (deprecation, compliance scope change, breaking API change, etc.) maps to specific files and actions

### Innovation Security Gates
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

## Creative Engine — Customer-Centric Feature Discovery (Phase 58)

### Overview
Automates competitor gap analysis, customer pain point discovery, and feature opportunity scouting from public review sites, community forums, and GitHub issues. Outputs ranked feature specs with justification. Separate from Innovation Engine — different domain (customer voice vs. technical signals), different scoring, different sources (D351).

### Pipeline
```
DISCOVER → EXTRACT → SCORE → RANK → GENERATE
```

1. **DISCOVER** — Auto-discover competitors from category pages; scan review sites, forums, GitHub issues
2. **EXTRACT** — Extract pain points from raw signals via deterministic keyword matching + sentiment detection (D354)
3. **SCORE** — 3-dimension composite score: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25)
4. **RANK** — Deduplicate, cluster, prioritize by composite score; detect trends (velocity/acceleration)
5. **GENERATE** — Template-based feature specs with justification, competitive analysis, user quotes (D356)

### Commands
```bash
# Full pipeline
python tools/creative/creative_engine.py --run --json
python tools/creative/creative_engine.py --run --domain "proposal management" --json

# Individual stages
python tools/creative/creative_engine.py --discover --domain "proposal management" --json
python tools/creative/creative_engine.py --scan --all --json
python tools/creative/creative_engine.py --extract --json
python tools/creative/creative_engine.py --score --json
python tools/creative/creative_engine.py --rank --top-k 20 --json
python tools/creative/creative_engine.py --generate --json

# Status
python tools/creative/creative_engine.py --status --json
python tools/creative/creative_engine.py --pipeline-report --json
python tools/creative/creative_engine.py --competitors --json
python tools/creative/creative_engine.py --trends --json
python tools/creative/creative_engine.py --specs --json

# Sub-tools
python tools/creative/source_scanner.py --scan --all --json
python tools/creative/source_scanner.py --list-sources --json
python tools/creative/competitor_discoverer.py --discover --domain "proposal management" --json
python tools/creative/competitor_discoverer.py --list --json
python tools/creative/competitor_discoverer.py --confirm --competitor-id <id> --json
python tools/creative/pain_extractor.py --extract-all --json
python tools/creative/gap_scorer.py --score-all --json
python tools/creative/gap_scorer.py --top --limit 20 --json
python tools/creative/gap_scorer.py --gaps --json
python tools/creative/trend_tracker.py --detect --json
python tools/creative/trend_tracker.py --report --json
python tools/creative/spec_generator.py --generate-all --json
python tools/creative/spec_generator.py --list --json

# Daemon mode
python tools/creative/creative_engine.py --daemon --json
```

### Architecture Decisions
- **D351:** Creative Engine is separate from Innovation Engine (different domain, scoring, sources)
- **D352:** Source adapters via function registry dict (web_scanner pattern)
- **D353:** Competitor auto-discovery is advisory-only (human must confirm)
- **D354:** Pain extraction is deterministic keyword/regex (air-gap safe)
- **D355:** 3-dimension scoring: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25)
- **D356:** Feature specs are template-based (no LLM, reproducible)
- **D357:** All tables append-only except creative_competitors (UPDATE for status transitions)
- **D358:** Reuses _safe_get(), _get_db(), _now(), _audit() helpers
- **D359:** Daemon mode respects quiet hours from config
- **D360:** High-scoring signals cross-register to innovation_signals

---

## Industry Research Engine — Deep Vertical Research (Phase 63)

### Overview
Standardized, reusable process for deeply researching any industry vertical and producing scored research dossiers that feed into child app generation. General-purpose from day one, with trading as the first vertical. Separate from Innovation Engine (technical self-improvement) and Creative Engine (customer pain points) — Research Engine does deep industry vertical analysis with regulatory mapping, academic scanning, build/buy analysis, and capability gap assessment.

### Pipeline (9 stages)
```
SCOPE → LANDSCAPE → REGULATE → COMMUNITY → ACADEMIC → BUILD_BUY → SYNTHESIZE → FORECAST → DOSSIER
```

### Session Lifecycle
```
created → scoping → scanning → synthesizing → dossier_ready → reviewed → child_app_triggered → archived
```

### Commands
```bash
# Full pipeline
python tools/research/research_engine.py --run --vertical trading --json

# Individual stages
python tools/research/research_engine.py --run-stage SCOPE --session-id "rsess-xxx" --json

# Session management
python tools/research/session_manager.py --create --vertical trading --name "Trading Research" --json
python tools/research/session_manager.py --list --json

# Vertical management
python tools/research/vertical_loader.py --load --json
python tools/research/vertical_loader.py --list --json

# Source scanning (8 streams)
python tools/research/source_scanner.py --scan --session-id "rsess-xxx" --json
python tools/research/source_scanner.py --list-sources --json

# Challenge scoring
python tools/research/challenge_scorer.py --cluster --session-id "rsess-xxx" --json
python tools/research/challenge_scorer.py --score --session-id "rsess-xxx" --json

# Regulatory mapping
python tools/research/regulatory_mapper.py --map --session-id "rsess-xxx" --json

# Capability mapping
python tools/research/capability_mapper.py --map --session-id "rsess-xxx" --json

# Build/buy analysis
python tools/research/build_buy_analyzer.py --analyze --session-id "rsess-xxx" --json

# Trend detection
python tools/research/trend_detector.py --detect --json

# Dossier generation
python tools/research/dossier_generator.py --generate --session-id "rsess-xxx" --json
python tools/research/dossier_generator.py --list --json

# YouTube video scanning (9th source stream)
python tools/research/youtube_scanner.py --scan --queries "topic keyword" --json
python tools/research/youtube_scanner.py --scan --urls "https://youtube.com/watch?v=xxx" --json
python tools/research/youtube_scanner.py --scan --channels "UCxxx" --json

# Forecast generation (cross-engine predictions)
python tools/research/forecast_generator.py --generate --session-id "rsess-xxx" --json
python tools/research/forecast_generator.py --get --session-id "rsess-xxx" --json

# Status
python tools/research/research_engine.py --status --json

# Daemon mode
python tools/research/research_engine.py --daemon --json
```

### Architecture Decisions
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

### 6-Dimension Challenge Scoring
```
market_demand:          0.25  (signal frequency, upvotes, citations)
regulatory_pressure:    0.20  (regulation count, enforcement 1.5x, deadlines 1.3x)
technical_complexity:   0.15  (academic paper density, patent activity)
competitive_saturation: 0.15  (inverse: fewer solutions = bigger opportunity)
icdev_readiness:        0.15  (ICDEV™ capability coverage score)
compliance_alignment:   0.10  (maps to existing ICDEV™ framework = 1.0)
```

### Available Verticals
| Vertical | Config | Key Regulatory Bodies |
|----------|--------|----------------------|
| Trading | `context/research/verticals/trading.json` | CFTC, NFA, SEC, FINRA, MiFID II, FCA |
| Healthcare | `context/research/verticals/healthcare.json` | HHS OCR, FDA, CMS, ONC |
| Defense | `context/research/verticals/defense.json` | DoD CIO, DISA, NSA, NIST |
| Fintech | `context/research/verticals/fintech.json` | OCC, FDIC, Fed Reserve, CFPB, SEC |
| Cybersecurity | `context/research/verticals/cybersecurity.json` | CISA, NIST, NSA, FTC |
| Logistics | `context/research/verticals/logistics.json` | FMCSA, DOT, CBP, FDA Food |

---

## RICOAS — Requirements Intake, COA & Approval System

### Overview
RICOAS transforms vague customer requirements into structured, decomposed, MBSE-traced, compliance-validated work items through AI-driven conversational intake. Three new capabilities:

1. **Requirements Analyst Agent** (port 8453) — Conversational intake, gap detection, SAFe decomposition, readiness scoring, document extraction
2. **Supply Chain Agent** (port 8454) — Dependency graph, SBOM aggregation, ISA lifecycle, CVE triage, NIST 800-161 SCRM
3. **Simulation Agent** (port 8455) — Digital Program Twin, 6-dimension what-if simulation, Monte Carlo, COA generation

### Intake Pipeline (5 Stages)
1. **Session Setup** — Create intake session with customer info, impact level, classification, ATO context
2. **Conversational Intake** — AI-guided Q&A extracting requirements, detecting ambiguities and gaps in real-time
3. **Document Upload** — Upload SOW/CDD/CONOPS, extract shall/must/should statements as structured requirements
4. **Gap Detection & Readiness** — 7-dimension scoring (completeness, clarity, feasibility, compliance, testability, devsecops_readiness, ai_governance_readiness), NIST gap analysis
5. **SAFe Decomposition** — Epic > Capability > Feature > Story > Enabler with WSJF scoring, T-shirt sizing, BDD criteria

### ATO Boundary Impact (4 Tiers)
| Tier | Criteria | ATO Impact |
|------|----------|------------|
| GREEN | No boundary change | None |
| YELLOW | Minor adjustment — new component within boundary | SSP addendum, possible POAM |
| ORANGE | Significant change — cross-boundary data flow | SSP revision, ISSO review |
| RED | ATO-invalidating — classification change, boundary expansion | **Full stop. Alternative COAs generated.** |

### COA Generation (3 + Alternatives)
- **Speed COA**: MVP scope (P1 only), 1-2 PIs, S-M cost, higher risk
- **Balanced COA**: P1+P2 scope, 2-3 PIs, M-L cost, moderate risk (recommended)
- **Comprehensive COA**: Full scope, 3-5 PIs, L-XL cost, lowest risk
- **Alternative COAs** (for RED items): Achieve same mission intent within existing ATO boundary

### Readiness Thresholds
- **0.7** — Proceed to decomposition
- **0.8** — Proceed to COA generation
- **0.9** — Proceed to implementation

---

## SaaS Multi-Tenancy Architecture (Phase 21)

### Overview
ICDEV™ is exposed as a multi-tenant SaaS platform. The SaaS layer **wraps** existing tools (D58) — it does NOT rewrite them. Each REST/MCP endpoint resolves the tenant, routes to their isolated database, calls the existing Python tool, and returns the result.

### Tenant Isolation by Impact Level
| IL | Compute | Database | Network |
|----|---------|----------|---------|
| IL2-IL4 | Dedicated K8s namespace | Dedicated PostgreSQL (or SQLite dev) | Network policy isolation |
| IL5 | Dedicated K8s namespace + node pool | Dedicated RDS instance | VPC peering |
| IL6 | Dedicated AWS sub-account (SIPR) | Isolated VPC PostgreSQL | Air-gapped |

### Subscription Tiers
| Feature | Starter | Professional | Enterprise |
|---------|---------|-------------|------------|
| Projects | 5 | 25 | Unlimited |
| Users | 3 | 15 | Unlimited |
| Impact Levels | IL2, IL4 | IL2-IL5 | IL2-IL6 |
| Auth | API key | API key + OAuth | API key + OAuth + CAC/PIV |
| Compute | Shared K8s NS | Dedicated K8s NS | Dedicated AWS account |
| Rate Limit | 60/min | 300/min | Unlimited |
| CLI Ceiling | scripted_intake only | All except container_execution | All 4 capabilities |

### Authentication (3 Methods)
1. **API Key** — `Authorization: Bearer icdev_...` → SHA-256 hash lookup in api_keys table
2. **OAuth 2.0/OIDC** — `Authorization: Bearer eyJ...` → JWT decode, JWKS verification, tenant resolution
3. **CAC/PIV** — `X-Client-Cert-CN` header → CN lookup in users table (nginx/ALB TLS termination)

### API Transport
- **REST API** — `POST/GET /api/v1/*` — standard HTTP JSON for generic clients
- **MCP Streamable HTTP** — `POST/GET/DELETE /mcp/v1/` — JSON-RPC 2.0 via Streamable HTTP (spec 2025-03-26) for Claude Code clients

### Key Components
| Component | File | Purpose |
|-----------|------|---------|
| Platform DB | `tools/saas/platform_db.py` | PostgreSQL/SQLite schema for tenants, users, keys, subscriptions |
| Tenant Manager | `tools/saas/tenant_manager.py` | Tenant CRUD, provisioning lifecycle, DB creation |
| Auth Middleware | `tools/saas/auth/middleware.py` | Extract/validate credentials, set tenant context |
| RBAC | `tools/saas/auth/rbac.py` | Role-based access control (5 roles × 9 categories) |
| API Gateway | `tools/saas/api_gateway.py` | Main Flask app: REST + MCP Streamable HTTP + auth + rate limiting |
| REST API | `tools/saas/rest_api.py` | Flask Blueprint with all v1 endpoints |
| MCP Streamable HTTP | `tools/saas/mcp_http.py` | MCP Streamable HTTP transport (spec 2025-03-26, session-based) |
| Tenant DB Adapter | `tools/saas/tenant_db_adapter.py` | Route tool DB calls to tenant's database |
| Rate Limiter | `tools/saas/rate_limiter.py` | Per-tenant rate limiting by tier |
| DB Compat | `tools/saas/db/db_compat.py` | SQLite ↔ PostgreSQL compatibility layer |
| PG Schema | `tools/saas/db/pg_schema.py` | Full ICDEV™ schema ported to PostgreSQL DDL |
| Artifact Delivery | `tools/saas/artifacts/delivery_engine.py` | Push artifacts to tenant S3/Git/SFTP |
| Bedrock Proxy | `tools/saas/bedrock/bedrock_proxy.py` | Route LLM calls to BYOK or shared pool |
| License Validator | `tools/saas/licensing/license_validator.py` | RSA-SHA256 offline license validation |
| Tenant Portal | `tools/saas/portal/app.py` | Web dashboard for tenant admin (pages: dashboard, projects, compliance, team, settings, profile, api_keys, usage, cmmc, phases, translations, oscal, prod-audit, ai-transparency, ai-accountability, code-quality, chat, audit) |
| NS Provisioner | `tools/saas/infra/namespace_provisioner.py` | Create per-tenant K8s namespace |

---

## Marketplace — Federated FORGE Asset Registry (Phase 22)

### Overview
Customer developer communities share skills, goals, hardprompts, context, args, and compliance extensions through a federated marketplace with mandatory security scanning, compliance validation, and governance enforcement. 100% air-gapped, integrated with Phase 21 SaaS infrastructure.

### Key Commands
```bash
# Publish a skill to tenant-local catalog
python tools/marketplace/publish_pipeline.py --asset-path /path --asset-type skill --tenant-id "tenant-abc" --publisher-user "user@mil" --json

# Search the marketplace
python tools/marketplace/search_engine.py --search "STIG checker" --json

# Check IL compatibility
python tools/marketplace/compatibility_checker.py --asset-id "asset-abc" --consumer-il IL5 --json

# Install an asset
python tools/marketplace/install_manager.py --install --asset-id "asset-abc" --tenant-id "tenant-abc" --json

# Review queue (ISSO/security officer)
python tools/marketplace/review_queue.py --pending --json
python tools/marketplace/review_queue.py --review --review-id "rev-abc" --reviewer-id "isso@mil" --decision approved --rationale "Passed review" --json

# Federation sync
python tools/marketplace/federation_sync.py --status --json
python tools/marketplace/federation_sync.py --promote --tenant-id "tenant-abc" --json
python tools/marketplace/federation_sync.py --pull --tenant-id "tenant-abc" --consumer-il IL5 --json

# Security scanning
python tools/marketplace/asset_scanner.py --asset-id "asset-abc" --version-id "ver-abc" --asset-path /path --json

# Catalog management
python tools/marketplace/catalog_manager.py --list --asset-type skill --json
python tools/marketplace/catalog_manager.py --get --slug "tenant-abc/my-skill" --json

# Provenance
python tools/marketplace/provenance_tracker.py --report --asset-id "asset-abc" --json
```

---

## CI/CD Integration (GitHub + GitLab)

### Trigger Methods
- **Webhook Server:** `python tools/ci/triggers/webhook_server.py` — receives POST events from GitHub (`/gh-webhook`) and GitLab (`/gl-webhook`)
- **Poll Trigger:** `python tools/ci/triggers/poll_trigger.py` — polls issues every 20 seconds

### Workflow Commands (in issue body or comments)
- `/icdev_plan` — Planning only
- `/icdev_build run_id:abc12345` — Build (requires prior plan run_id)
- `/icdev_test run_id:abc12345` — Test
- `/icdev_review run_id:abc12345` — Review
- `/icdev_sdlc` — Complete lifecycle: Plan → Build → Test → Review
- `/icdev_plan_build` — Plan + Build
- `/icdev_plan_build_test` — Plan + Build + Test
- `/icdev_plan_build_test_review` — Plan + Build + Test + Review

### Claude Code Slash Commands (used by workflows)
| Command | Purpose |
|---------|---------|
| `/classify_issue` | Classify issue as /chore, /bug, /feature, /patch |
| `/classify_workflow` | Extract ICDEV™ workflow command from text |
| `/generate_branch_name` | Generate branch: `<type>-issue-<num>-icdev-<id>-<name>` |
| `/implement` | Implement a plan with CUI markings |
| `/commit` | Generate commit: `<agent>: <type>: <message>` |
| `/pull_request` | Create PR (GitHub) or MR (GitLab) |

### Platform Auto-Detection
VCS detects GitHub vs GitLab from `git remote get-url origin`. Uses `gh` CLI for GitHub, `glab` CLI for GitLab.

### Bot Loop Prevention
All bot comments include `[ICDEV™-BOT]`. Webhooks ignore comments with this identifier.
