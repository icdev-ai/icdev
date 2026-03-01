# Phase 59 — GovCon Intelligence: Capture-to-Delivery Flywheel

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 59 |
| Title | GovCon Intelligence: Capture-to-Delivery Flywheel |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 21 (SaaS Multi-Tenancy), Phase 35 (Innovation Engine), Phase 58 (Creative Engine) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-27 |

---

## 1. Problem Statement

Government contracting capture is a manual, fragmented process. Business development teams monitor SAM.gov by hand, read hundreds of pages of solicitations looking for relevant requirements, assess capability fit in spreadsheets, and draft proposal responses from scratch for every RFP. Competitive intelligence comes from word-of-mouth. Compliance matrices are populated manually. There is no systematic way to know which requirement patterns are trending across the federal marketplace, which capabilities have gaps, or which competitors are winning in a given NAICS code.

ICDEV already builds ATO-ready government applications, but without a capture pipeline, the platform has no systematic way to:
- Discover relevant SAM.gov opportunities automatically
- Extract and cluster recurring "shall/must/will" requirement patterns across RFPs
- Map those requirements to ICDEV's existing capabilities with quantitative coverage scores
- Identify capability gaps and feed them into the Innovation Engine for prioritized enhancement
- Draft proposal responses using two-tier LLM routing with knowledge base evidence
- Auto-populate L/M/N compliance matrices from coverage scores
- Track competitor award patterns for competitive intelligence
- Generate strategic questions for government Q&A periods
- Track RFP amendments and version changes
- Close the flywheel: Win contracts, deliver ICDEV free on-prem, repeat

Phase 59 closes this gap with a 4-stage capture pipeline (DISCOVER, EXTRACT, MAP, DRAFT) supported by a knowledge base, competitor profiler, question generator, amendment tracker, and question exporter — all integrated into the existing GovProposal lifecycle tracker and dashboard.

---

## 2. Goals

1. Scan SAM.gov Opportunities API for solicitations, pre-solicitations, RFIs, and award notices filtered by 8 NAICS codes
2. Extract "shall/must/will" obligation statements via deterministic regex (no LLM) and classify into 9 domain categories
3. Cluster requirement patterns by keyword fingerprint overlap using union-find (D364)
4. Map requirement patterns to a declarative ICDEV capability catalog (~30 entries) with quantitative coverage scoring
5. Grade coverage as L (>=0.80), M (0.40-0.79), or N (<0.40) and auto-populate proposal compliance matrices
6. Identify capability gaps and cross-register high-priority gaps to the Innovation Engine as signals
7. Draft proposal responses via two-tier LLM (qwen3 compact draft, Claude review/polish) with template fallback
8. Build a searchable knowledge base of reusable content blocks by category, domain, and volume type
9. Track contract awards and build competitor profiles with leaderboard and side-by-side comparison
10. Generate strategic questions from RFP ambiguity analysis with deterministic priority scoring
11. Track RFP amendments with auto-diff between versions using Python stdlib `difflib`
12. Export formatted question documents (HTML) for government Q&A period submissions
13. Provide 3 dedicated dashboard pages (/govcon, /govcon/requirements, /govcon/capabilities) plus GovCon action bar on proposal detail pages
14. Implement daemon mode with configurable scheduling and quiet hours
15. Cross-register SAM.gov signals to both the Innovation Engine and Creative Engine (D361)

---

## 3. Architecture

```
                    GovCon Intelligence Flywheel
     ┌────────────────────────────────────────────────────────┐
     │              args/govcon_config.yaml                    │
     │  (SAM.gov API, extraction rules, mapping, drafting,    │
     │   business model, scheduling, questions, amendments)    │
     └───────────────────────┬────────────────────────────────┘
                             │
     ┌───────────┬───────────┼───────────┬──────────────┐
     ↓           ↓           ↓           ↓              ↓
  DISCOVER    EXTRACT       MAP        DRAFT        SUPPORT
  sam_scanner  requirement   capability  response     knowledge_base
  award_tracker extractor   mapper      drafter      competitor_profiler
     │           │          gap_analyzer compliance   question_generator
     │           │            │          populator    amendment_tracker
     │           │            │            │          question_exporter
     ↓           ↓            ↓            ↓              │
  sam_gov_    rfp_shall_    icdev_      proposal_         │
  opportunities statements  capability  section_          │
  govcon_awards rfp_req_    _map        drafts            │
     │         patterns       │            │              │
     │           │            │            │              │
     └─────┬─────┘            │            │              │
           ↓                  ↓            ↓              ↓
     Innovation Engine   Gap Analysis   proposal_     Dashboard API
     Creative Engine     (cross-reg)    compliance    /api/govcon/*
     (D361 cross-reg)                   _matrix       20+ endpoints
                                           │
                                           ↓
                                    GovProposal
                                    Lifecycle
                                    (/proposals)

     ┌──────────────────────────────────────────────┐
     │           Dashboard Pages                     │
     │  /govcon            — Pipeline status          │
     │  /govcon/requirements — Pattern analysis       │
     │  /govcon/capabilities — Coverage & gaps        │
     │  /proposals/<id>     — GovCon action bar       │
     └──────────────────────────────────────────────┘
```

### Key Design Principles

- **Deterministic extraction** — Regex-based "shall/must/will" mining, no LLM required (D362)
- **Declarative capability catalog** — JSON file defines ICDEV capabilities with keywords, tools, and compliance controls (D363)
- **Two-tier LLM drafting** — qwen3 drafts compact structured responses, Claude reviews and polishes (D365)
- **Template fallback** — If LLM unavailable, template-based responses ensure air-gap operation
- **Append-only storage** — Shall statements, patterns, and drafts are immutable time-series (D371, D373)
- **Cross-registration** — SAM.gov signals feed both Innovation Engine and Creative Engine for trend detection (D361)
- **HITL gate** — AI drafts require human approval before flowing to proposal sections (D373)

---

## 4. Implementation

### Component 1: SAM.gov Opportunity Scanner (`tools/govcon/sam_scanner.py`)

Polls the SAM.gov Opportunities API v2 for solicitations, pre-solicitations, RFIs, and award notices matching configured NAICS codes. Follows the `SOURCE_SCANNERS` function registry pattern from the Creative Engine (D352).

- **API key** from `SAM_GOV_API_KEY` env var (D366)
- **Rate limiting:** 10 req/sec, 10K/day with 150ms delay between requests (D370)
- **Deduplication** by solicitation_number + content_hash
- **Circuit breaker** integration via `InMemoryCircuitBreaker` (optional)
- **Air-gapped mode** disables scanning, serves cached data only

### Component 2: Requirement Extractor (`tools/govcon/requirement_extractor.py`)

Extracts obligation statements from opportunity descriptions using deterministic regex (D362) and classifies them into 9 domain categories via keyword overlap scoring.

- **10 shall patterns:** shall, must, is required to, will provide, will deliver, will maintain, will ensure, will demonstrate, will comply, will support
- **9 domain categories:** devsecops, ai_ml, ato_rmf, cloud, security, compliance, agile, data, management
- **Keyword fingerprint clustering** using union-find algorithm (D364)
- **Pattern frequency tracking** enables trend analysis across RFPs (D371)

### Component 3: Capability Mapper (`tools/govcon/capability_mapper.py`)

Matches requirement patterns against the ICDEV capability catalog using keyword overlap scoring. Coverage grades:

| Grade | Score Range | Compliance Status |
|-------|-------------|-------------------|
| L | >= 0.80 | Compliant |
| M | 0.40 - 0.79 | Partial |
| N | < 0.40 | Non-compliant (gap) |

### Component 4: Gap Analyzer (`tools/govcon/gap_analyzer.py`)

Identifies unmet requirements where ICDEV coverage is insufficient. Produces:
- Prioritized gap list (frequency x gap severity)
- Enhancement recommendations per gap
- Domain-level gap heatmap
- Innovation Engine cross-registration for high-priority gaps (D361)

### Component 5: Response Drafter (`tools/govcon/response_drafter.py`)

Auto-drafts proposal responses using the two-tier LLM architecture (D365):
1. **qwen3** drafts compact structured response (~400 words, bullet points)
2. **Claude** reviews and polishes (quality, compliance accuracy, tone)
3. Store in `proposal_section_drafts` with `status='draft'`
4. Human reviews, approves, content flows to `proposal_sections`

Product-level response detection: when a requirement mentions 2+ product trigger keywords, the drafter uses whole-product templates instead of individual capability templates.

### Component 6: Compliance Populator (`tools/govcon/compliance_populator.py`)

Auto-populates the L/M/N compliance matrix in `proposal_compliance_items` from capability coverage scores. Bridges the coverage grading directly into the existing GovProposal compliance matrix format.

### Component 7: Knowledge Base (`tools/govcon/knowledge_base.py`)

CRUD for reusable proposal content blocks, organized by:
- **11 categories:** capability_description, approach, staffing, tools_used, past_performance, differentiator, management_approach, transition_plan, product_overview, integrated_solution, customer_value
- **10 domains:** devsecops, ai_ml, ato_rmf, cloud, security, compliance, agile, data, management, general
- **5 volume types:** technical, management, past_performance, cost, staffing

Keyword search with usage tracking. Seed command populates initial blocks from the capability catalog.

### Component 8: Award Tracker (`tools/govcon/award_tracker.py`)

Polls SAM.gov for award notices, extracts vendor data, and builds the competitive intelligence database. Auto-creates entries in `creative_competitors` with `source='sam_gov'`.

### Component 9: Competitor Profiler (`tools/govcon/competitor_profiler.py`)

Aggregates award data into vendor profiles with:
- Total awards and contract value per vendor
- Common agencies and NAICS codes
- Win rate estimates by domain
- Leaderboard rankings
- Side-by-side vendor comparison

### Component 10: Question Generator (`tools/govcon/question_generator.py`)

Analyzes RFP text for ambiguities, gaps, and strategic opportunities using deterministic regex/keyword extraction (D-QTG-1). Nine analysis categories:
1. Vague/ambiguous language ("as needed", "TBD", "appropriate")
2. Missing evaluation criteria weights
3. Unclear period of performance / timeline
4. Missing data rights / IP provisions
5. L vs M section misalignment
6. Small business / set-aside ambiguity
7. Unclear security / compliance requirements
8. Missing or unusual contract terms
9. Strategic advantage questions

Priority scoring: `priority_score = category_weight * ambiguity_level * strategic_value` (D21 pattern). High >= 7.0, Medium >= 4.0, Low < 4.0.

### Component 11: Amendment Tracker (`tools/govcon/amendment_tracker.py`)

Tracks RFP amendments/revisions with auto-diff between versions using Python stdlib `difflib` (D-QTG-3, air-gap safe). Features:
- Upload amendment from file or pasted text
- Auto-increment version numbers per opportunity
- Compute unified diff between consecutive versions
- Record government Q&A responses linked to questions
- Auto-update question status to 'answered' when response recorded

### Component 12: Question Exporter (`tools/govcon/question_exporter.py`)

Exports questions to formatted HTML for government Q&A period submission. Includes CUI banner, solicitation number, title, company name, date, and numbered questions organized by category (D-QTG-4).

### Component 13: Pipeline Orchestrator (`tools/govcon/govcon_engine.py`)

Unified orchestrator running the 4-stage pipeline: DISCOVER, EXTRACT, MAP, DRAFT. Daemon mode with configurable scheduling and quiet hours. Status and reporting commands.

### Component 14: Dashboard API (`tools/dashboard/api/govcon.py`)

Flask Blueprint with 20+ REST endpoints bridging all GovCon tools into the dashboard and proposal lifecycle. Integration points:
- `sam_scanner.py` -> `proposal_opportunities` (auto-create from SAM.gov)
- `requirement_extractor` -> `rfp_shall_statements` (extract "shall" from opp)
- `capability_mapper` -> `icdev_capability_map` (score coverage per req)
- `compliance_populator` -> `proposal_compliance_matrix` (auto-populate L/M/N)
- `response_drafter` -> `proposal_section_drafts` (AI draft -> human review)
- `gap_analyzer` -> `innovation_signals` (cross-register gaps)
- `knowledge_base` -> `proposal_knowledge_base` (reusable content blocks)
- `competitor_profiler` -> `govcon_awards` (vendor intelligence)

---

## 5. Database

### `sam_gov_opportunities` (allows UPDATE for sync)
SAM.gov opportunity records with solicitation number, title, agency, NAICS, notice type, description, response deadline, content hash. Updated on re-scan for status changes.

### `rfp_shall_statements` (append-only)
Individual extracted obligation statements linked to opportunities. Includes statement text, type (shall/must/will), domain classification, confidence score, content hash for dedup.

### `rfp_requirement_patterns` (append-only)
Clustered requirement patterns with frequency tracking across opportunities. Includes pattern text, keyword fingerprint, domain, frequency count, first/last seen timestamps (D371).

### `icdev_capability_map` (append-only)
Bridge table mapping requirement patterns to ICDEV capabilities with coverage scores and L/M/N grades.

### `govcon_awards` (append-only)
Award notice records with awardee name, agency, amount, NAICS, contract type, award date.

### `proposal_section_drafts` (append-only)
AI-generated draft responses linked to shall statements and opportunities. Includes draft text, generation method (two_tier/template), confidence score, status (draft/approved/rejected) (D373).

### `proposal_knowledge_base` (allows UPDATE)
Reusable content blocks with title, content, category, domain, volume type, keywords, usage count. Updated for content refinement (D368).

### `rfp_questions` (append-only)
Auto-generated strategic questions with category, priority score, ambiguity level, strategic value, RFP reference section, status (draft/approved/submitted/answered).

### `rfp_amendments` (append-only)
RFP amendment versions with full text, version number, diff from previous version, upload timestamp.

### `rfp_question_responses` (append-only)
Government Q&A responses linked to questions. Records response text, source amendment, response date.

---

## 6. Configuration

### `args/govcon_config.yaml`

| Section | Purpose |
|---------|---------|
| `sam_gov` | SAM.gov API URL, key env var, poll interval, rate limits (10 req/sec, 10K/day), NAICS codes (8), notice types, lookback days |
| `requirement_extraction` | 10 shall patterns, 9 domain categories with keyword lists, min pattern frequency, clustering thresholds (D362, D364) |
| `capability_mapping` | Catalog path, min keyword overlap (2), coverage thresholds: L >= 0.80, M >= 0.40 (D363) |
| `response_drafting` | LLM function name, max draft length (500 words), evidence/tool reference inclusion, confidence threshold (0.70), product detection config (D365) |
| `award_tracking` | Poll interval (24h), lookback (90 days), auto-create competitor flag (D367) |
| `knowledge_base` | Max blocks per draft (3), search top-k (5), usage tracking, 11 categories (D368) |
| `business_model` | Licensing options (on_prem_free, saas_paid, negotiated, tbd), 3 delivery tiers (core/standard/enterprise), internal-only components list (D372, D374) |
| `cross_registration` | Innovation Engine signal category, min score; Creative Engine source type (D361) |
| `scheduling` | Daemon mode, scan intervals (6h opp, 24h awards, 12h clustering), quiet hours (D359 pattern) |
| `questions_to_government` | Category weights (6 categories), priority thresholds, max auto-questions (50), dedup threshold, status transitions, export settings, amendment diff settings (D-QTG-1 through D-QTG-5) |

### `context/govcon/icdev_capability_catalog.json`

Declarative capability catalog with ~30 entries including:
- **Product-level entries** (`prod-*`): Whole products (ICDEV Platform, CPMP) with key capabilities, customer value, compliance controls
- **Component-level entries** (`cap-*`): Individual capabilities (TDD/BDD, NIST 800-53, Zero Trust, etc.) with keywords, tools, and evidence

---

## 7. Dashboard

### `/govcon` — Pipeline Dashboard
SAM.gov scan status, recent opportunities table, domain distribution chart, pipeline stage controls (trigger scan, extract, map, draft).

### `/govcon/requirements` — Requirement Analysis
Pattern frequency tables, domain heatmap, statement type distribution, trend analysis showing which requirement categories are appearing more frequently across RFPs.

### `/govcon/capabilities` — Coverage & Gaps
L/M/N coverage breakdown by domain, gap list with enhancement recommendations, capability catalog browser, domain-level coverage heatmap.

### `/proposals/<id>` — GovCon Intelligence Action Bar
Integrated into existing proposal detail page with action buttons:
- **Extract** — Mine shall statements from opportunity description
- **Map** — Match requirements to ICDEV capabilities
- **Compliance** — Auto-populate L/M/N compliance matrix
- **Draft** — Generate AI response drafts for all shall statements
- **Bid Rec** — Bid/no-bid recommendation based on coverage scores

Plus an **AI Drafts** tab showing generated responses with approve/reject workflow.

### `/proposals` — Proposal List
Opportunity listing with stat grid, new opportunity modal, status indicators.

### `/proposals/<id>/sections/<id>` — Section Detail
14-step status pipeline (Shipley color team workflow), info grid, notes, compliance items, findings, dependencies, status history, advance workflow buttons.

---

## 8. Security Gates

The GovCon Intelligence pipeline inherits existing security gates and adds domain-specific controls:

- **SAM.gov API key** managed via `SAM_GOV_API_KEY` env var, never stored in code (D366)
- **Rate limiting** enforced at 10 req/sec, 10K/day to comply with SAM.gov API terms (D370)
- **Append-only audit trail** for all pipeline actions via `audit_trail` table (NIST AU-2)
- **HITL gate** on all AI drafts — no auto-promotion to proposal sections (D373)
- **Internal-only components** (govcon, creative, innovation, govproposal, saas, marketplace_admin) are never included in customer deliveries (D374)
- **CUI markings** applied to all generated content and export documents
- **Content hash deduplication** prevents duplicate opportunity and question processing
- **Air-gapped degradation** — scanning disabled, all other stages (extraction, mapping, compliance population) work from cached data
- **Question export** includes CUI banner header and footer per classification requirements

---

## 9. Verification

```bash
# Full pipeline
python tools/govcon/govcon_engine.py --run --json
python tools/govcon/govcon_engine.py --status --json
python tools/govcon/govcon_engine.py --pipeline-report --json

# Individual stages
python tools/govcon/govcon_engine.py --stage discover --json
python tools/govcon/govcon_engine.py --stage extract --json
python tools/govcon/govcon_engine.py --stage map --json
python tools/govcon/govcon_engine.py --stage draft --json

# SAM.gov scanning
python tools/govcon/sam_scanner.py --scan --json
python tools/govcon/sam_scanner.py --scan --naics 541512 --json
python tools/govcon/sam_scanner.py --list-cached --json
python tools/govcon/sam_scanner.py --history --days 30 --json
python tools/govcon/sam_scanner.py --stats --json

# Requirement extraction
python tools/govcon/requirement_extractor.py --extract-all --json
python tools/govcon/requirement_extractor.py --patterns --json
python tools/govcon/requirement_extractor.py --patterns --domain devsecops --json
python tools/govcon/requirement_extractor.py --patterns --min-frequency 3 --json
python tools/govcon/requirement_extractor.py --trends --json

# Capability mapping and gaps
python tools/govcon/capability_mapper.py --map-all --json
python tools/govcon/capability_mapper.py --coverage --json
python tools/govcon/capability_mapper.py --gaps --json
python tools/govcon/gap_analyzer.py --analyze --json
python tools/govcon/gap_analyzer.py --recommendations --json
python tools/govcon/gap_analyzer.py --heatmap --json
python tools/govcon/gap_analyzer.py --register-innovation --json

# Response drafting
python tools/govcon/response_drafter.py --draft-all --opp-id <id> --json
python tools/govcon/response_drafter.py --draft --shall-id <id> --json
python tools/govcon/response_drafter.py --list-drafts --opp-id <id> --json
python tools/govcon/response_drafter.py --approve --draft-id <id> --json
python tools/govcon/response_drafter.py --template-draft --shall-id <id> --json

# Compliance population
python tools/govcon/compliance_populator.py --populate --opp-id <id> --json
python tools/govcon/compliance_populator.py --summary --opp-id <id> --json
python tools/govcon/compliance_populator.py --export-matrix --opp-id <id> --json

# Knowledge base
python tools/govcon/knowledge_base.py --list --json
python tools/govcon/knowledge_base.py --search --query "DevSecOps pipeline" --json
python tools/govcon/knowledge_base.py --seed --json

# Award tracking and competitor intelligence
python tools/govcon/award_tracker.py --scan --json
python tools/govcon/award_tracker.py --list --json
python tools/govcon/award_tracker.py --stats --json
python tools/govcon/competitor_profiler.py --leaderboard --json
python tools/govcon/competitor_profiler.py --compare --vendors "Booz Allen,Deloitte" --json

# Question generation and Q&A lifecycle
python tools/govcon/question_generator.py --generate --opp-id <id> --json
python tools/govcon/question_generator.py --list --opp-id <id> --json
python tools/govcon/question_generator.py --stats --opp-id <id> --json

# Amendment tracking
python tools/govcon/amendment_tracker.py --upload --opp-id <id> --file <path> --title "Amendment 1" --json
python tools/govcon/amendment_tracker.py --diff --amendment-id <id> --json
python tools/govcon/amendment_tracker.py --list --opp-id <id> --json

# Question export
python tools/govcon/question_exporter.py --export --opp-id <id> --json
python tools/govcon/question_exporter.py --export --opp-id <id> --output /path/to/output.html

# Daemon mode
python tools/govcon/govcon_engine.py --daemon --json

# Dashboard
# Start: python tools/dashboard/app.py
# Navigate: /govcon, /govcon/requirements, /govcon/capabilities
# Navigate: /proposals/<id> -> GovCon Intelligence action bar
```

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D361 | SAM.gov scanner cross-registered in Innovation + Creative engines | Enables trend detection ("is cATO appearing more in RFPs?") and competitive gap analysis via existing pipelines |
| D362 | Deterministic regex for shall extraction | Air-gap safe, reproducible, no LLM dependency. 10 patterns cover obligation language in federal solicitations |
| D363 | Declarative JSON capability catalog | Add/remove capabilities without code changes (D26 pattern). Product-level and component-level entries |
| D364 | Keyword fingerprint clustering (union-find) | Groups similar requirements across RFPs without LLM. Shared keyword threshold configurable |
| D365 | Two-tier LLM: qwen3 drafts, Claude reviews | Consistent with project two-tier architecture. Context budget savings: qwen3 outputs compact structured draft, Claude receives short input. Template fallback for air-gap |
| D366 | SAM.gov API key via env var | Never stored in code or config files. Graceful degradation when absent |
| D367 | Separate govcon_awards table for award data | Award notices have different schema than opportunities. Enables competitive profiling aggregations |
| D368 | Knowledge base in DB for searchability | Keyword search over content blocks. Usage tracking informs which blocks are most effective |
| D369 | GovCon tools in tools/govcon/ (separate domain) | Clean separation from existing tools. Internal-only — never delivered to customers |
| D370 | Rate limiting: 10 req/sec, 10K/day | Complies with SAM.gov API terms of service. 150ms delay between requests |
| D371 | Patterns are append-only time-series | Enables frequency trend analysis. Pattern growth over time shows market direction |
| D372 | licensing_model field on proposal_opportunities | Tracks business model decision per opportunity. Four options: on_prem_free, saas_paid, negotiated, tbd |
| D373 | Drafts stored append-only, HITL gate before section | AI drafts are never auto-promoted. Human reviews quality, compliance accuracy, and tone before approval flows content to proposal_sections |
| D374 | Customer delivery tier to deployment profile mapping | Three tiers (core/standard/enterprise) map to deployment profiles. Internal-only components (govcon, creative, innovation, govproposal, saas, marketplace_admin) excluded from all tiers |
| D-QTG-1 | Deterministic priority scoring for questions | Category weight x ambiguity level x strategic value. Reproducible, no LLM (D21 pattern) |
| D-QTG-2 | SHA-256 content hash dedup for questions | Prevents duplicate questions. 0.85 similarity threshold |
| D-QTG-3 | Python stdlib difflib for amendment diffs | Air-gap safe, zero deps. Unified diff with configurable context lines |
| D-QTG-4 | HTML export for government submission format | Print-friendly, CUI banner included, organized by category, matches Q&A submission conventions |
| D-QTG-5 | Question status state machine | draft -> approved -> submitted -> answered. Government responses auto-update status |

---

## 11. Files

### New Files (18)
| File | Purpose |
|------|---------|
| `tools/govcon/__init__.py` | Package |
| `tools/govcon/sam_scanner.py` | SAM.gov opportunity scanning |
| `tools/govcon/requirement_extractor.py` | Shall statement extraction and pattern clustering |
| `tools/govcon/capability_mapper.py` | Requirement-to-capability coverage scoring |
| `tools/govcon/gap_analyzer.py` | Gap identification and enhancement recommendations |
| `tools/govcon/response_drafter.py` | Two-tier LLM response drafting |
| `tools/govcon/compliance_populator.py` | L/M/N compliance matrix auto-population |
| `tools/govcon/knowledge_base.py` | Reusable content block CRUD |
| `tools/govcon/award_tracker.py` | Award notice scanning and vendor extraction |
| `tools/govcon/competitor_profiler.py` | Competitive intelligence from award data |
| `tools/govcon/govcon_engine.py` | Pipeline orchestrator with daemon mode |
| `tools/govcon/question_generator.py` | Strategic question auto-generation |
| `tools/govcon/amendment_tracker.py` | RFP amendment version tracking and diff |
| `tools/govcon/question_exporter.py` | HTML question export for government submission |
| `tools/dashboard/api/govcon.py` | Flask Blueprint with 20+ REST endpoints |
| `tools/dashboard/templates/govcon/pipeline.html` | GovCon pipeline dashboard page |
| `tools/dashboard/templates/govcon/requirements.html` | Requirement analysis dashboard page |
| `tools/dashboard/templates/govcon/capabilities.html` | Coverage and gaps dashboard page |
| `context/govcon/icdev_capability_catalog.json` | Declarative capability catalog (~30 entries) |
| `args/govcon_config.yaml` | GovCon configuration (SAM.gov, extraction, mapping, drafting, questions, scheduling) |
| `goals/govcon_intelligence.md` | Goal document |

### Modified Files
| File | Change |
|------|--------|
| `tools/db/init_icdev_db.py` | +10 CREATE TABLE statements (sam_gov_opportunities, rfp_shall_statements, rfp_requirement_patterns, icdev_capability_map, govcon_awards, proposal_section_drafts, proposal_knowledge_base, rfp_questions, rfp_amendments, rfp_question_responses) |
| `tools/dashboard/app.py` | +govcon Blueprint registration, +/govcon routes |
| `tools/dashboard/templates/proposals/detail.html` | +GovCon Intelligence action bar, +AI Drafts tab |
| `tools/innovation/web_scanner.py` | +scan_sam_gov_for_innovation() source adapter |
| `tools/creative/source_scanner.py` | +scan_sam_gov_for_creative() source adapter |
| `CLAUDE.md` | +D361-D374, D-QTG-1 through D-QTG-5, +tables, +commands, +config |
| `tools/manifest.md` | +GovCon Intelligence section |
| `goals/manifest.md` | +GovCon Intelligence entry |
| `args/govcon_config.yaml` | +questions_to_government section |

---

## 12. Business Model Integration

Phase 59 closes the business flywheel:

```
SAM.gov RFPs
    → Mine requirement patterns
        → Map to ICDEV capabilities
            → Identify gaps → Build enhancements (Innovation Engine)
                → Draft proposals via GovProposal
                    → Win contract
                        → Deliver ICDEV free on-prem
                            → Customer success drives past performance
                                → Repeat
```

### Customer Delivery Tiers (D374)

| Tier | Modules | Recommended For |
|------|---------|-----------------|
| Core (8) | Builder, security, testing, compliance, CI/CD, dashboard | Task order deliverables, simple compliance automation |
| Standard (12) | + ZTA, RICOAS intake, infrastructure, monitoring | Program-level delivery, DevSecOps transformation |
| Enterprise (18) | + MBSE, modernization, gateway, multi-agent | Enterprise-wide digital transformation, MDAP programs |

### Internal-Only Components
These are NEVER included in customer delivery:
- `govcon` — SAM.gov scanning, requirement mining, competitive intelligence
- `creative` — Competitor gap analysis, pain point extraction
- `innovation` — Self-improvement engine, web intelligence
- `govproposal` — Proposal writing, RFX AI drafting, color reviews
- `saas` — Multi-tenant SaaS platform layer
- `marketplace_admin` — Marketplace administration

---

## 13. Edge Cases

- **SAM.gov API key not set** — Graceful degradation: scan returns empty with descriptive error message, all other stages operate on cached data
- **No opportunities found for configured NAICS** — Returns empty result with suggestion to broaden NAICS filter
- **LLM provider unavailable** — Drafting falls back to template-based responses; compliance population still works (deterministic)
- **Quiet hours** — Daemon skips pipeline run, resumes after quiet window
- **Duplicate opportunities** — Deduplication by solicitation_number + content_hash
- **Duplicate questions** — SHA-256 content hash dedup with 0.85 similarity threshold
- **Air-gapped environment** — SAM.gov scanning disabled; extraction, mapping, compliance, and question generation work from cached data; template-based drafting replaces LLM drafting
