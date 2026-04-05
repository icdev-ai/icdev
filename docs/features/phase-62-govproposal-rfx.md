# Phase 62 — GovProposal: RFX AI Proposal Engine

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 62 |
| Title | GovProposal: RFX AI Proposal Engine |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 59 (GovCon Intelligence), Phase 60 (CPMP), Phase 21 (SaaS Multi-Tenancy) |
| Author | ICDEV™ Architect Agent |
| Date | 2026-02-28 |

---

## 1. Problem Statement

Phases 59 and 60 built the GovCon Intelligence capture pipeline and the Contract Performance Management Portal, but four gaps remained:

1. **No end-to-end proposal lifecycle** — Opportunities were discovered and requirements extracted (Phase 59), but there was no structured workflow for tracking an opportunity from capture through volume planning, section drafting, color reviews, and final submission. Business development teams still managed proposal status in spreadsheets.

2. **No AI-assisted response drafting** — Requirement extraction produced "shall/must" statements and capability scores, but converting those into Government-quality proposal responses required manual writing. The two-tier LLM architecture (qwen3 draft → Claude review) existed in concept but was not wired to a proposal drafting pipeline with knowledge base evidence.

3. **No proposal-to-contract bridge** — When a proposal was awarded, teams manually recreated contract data (CLINs, WBS, deliverables) in the CPMP. There was no automated bridge to carry proposal metadata, compliance matrix, and staffing data forward into post-award management.

4. **No parent-only isolation** — GovProposal is an ICDEV™ internal capability for business development — it should NOT be included in child applications generated for customers. No mechanism existed to exclude GovProposal tools, routes, and templates from child app scaffolding.

Phase 62 closes these gaps with a 14-step proposal lifecycle, AI-powered response drafting with knowledge base, explicit proposal-to-contract bridging, and clean feature-flag isolation via `ICDEV_GOVCON_ENABLED`.

---

## 2. Goals

1. Build a full proposal lifecycle tracker with 14-step status pipeline (draft → awarded)
2. Integrate two-tier LLM response drafting (qwen3 compact draft → Claude review) with template fallback
3. Create a reusable knowledge base of content blocks (11 categories × 10 domains)
4. Support volume-based proposal structure (Technical, Management, Cost, Staffing, Past Performance)
5. Implement color review workflow (Red → Yellow → Green → Final)
6. Auto-populate L/M/N compliance matrices from Phase 59 capability coverage scores
7. Track section dependencies for cross-reference consistency
8. Bridge won proposals to CPMP contracts with pre-populated metadata
9. Integrate RFP question generation and amendment tracking from Phase 59
10. Provide dashboard pages for opportunity list, detail views, and section editing
11. Feature-flag isolate all GovProposal/CPMP/GovCon routes via `ICDEV_GOVCON_ENABLED`
12. Exclude GovProposal tools from child app generation via `PARENT_ONLY_DIRS`

---

## 3. Architecture

### 3.1 Proposal Lifecycle (14-Step Status Pipeline)

```
draft → under_development → draft_complete → review_assigned
     → red_review → yellow_review → green_review → color_reviews_complete
     → final_review → final_approved → submitted → awarded
```

Each transition is recorded in `proposal_status_history` (append-only, NIST AU-2). Backward transitions are allowed for rework but always create a new history entry.

### 3.2 Volume Structure

| Volume | Typical Content |
|--------|-----------------|
| Technical | Technical approach, architecture, methodology |
| Management | Project management, staffing, quality control |
| Cost | Pricing, labor rates, BOEs |
| Staffing | Key personnel, org chart, resumes |
| Past Performance | Case studies, CPARS references |
| Small Business | Subcontracting plan, SB goals |

Each volume contains ordered sections. Sections have their own 14-step status pipeline.

### 3.3 Two-Tier LLM Response Drafting (D365)

```
Shall Statement + Capability Matches + Knowledge Base Blocks
    ↓
qwen3 Worker (compact draft, ~400 words, bullet points)
    ↓
Claude Reviewer (polish: quality, compliance, tone)
    ↓
proposal_section_drafts (status='draft', awaiting HITL approval)
    ↓
Human approves → proposal_sections (status='under_development')
```

**Fallback chain:**
- qwen3 unavailable → Claude drafts directly
- Claude unavailable → Template-based response
- Both unavailable → Structured error with human guidance

**LLM routing** via `tools/llm/router.py`:
- Function `proposal_drafting` registered in `args/llm_config.yaml` under `two_tier.worker_functions`
- qwen3 produces compact structured output (~400 words max)
- Claude receives: compact review prompt + original task + short draft
- Net result: Claude's input tokens are LESS than if Claude did the full task alone

### 3.4 Knowledge Base (D368)

Reusable content blocks organized by:

**11 Categories:**
capability_description, approach, staffing, tools_used, past_performance, differentiator, management_approach, transition_plan, product_overview, integrated_solution, customer_value

**10 Domains:**
devsecops, ai_ml, ato_rmf, cloud, security, compliance, agile, data, management, general

**Usage tracking:** Each block records how many times it has been used in drafts, enabling quality-based selection (frequently-used blocks are proven content).

### 3.5 Compliance Matrix Population

Phase 59's `capability_mapper.py` produces quantitative coverage scores per requirement. These scores auto-populate the proposal compliance matrix:

| Score | Grade | Meaning |
|-------|-------|---------|
| >= 0.80 | L (Low Risk) | Full capability exists |
| 0.40 - 0.79 | M (Moderate Risk) | Partial capability, augmentation needed |
| < 0.40 | N (Not Met) | Gap — Innovation Engine notified |

### 3.6 Proposal-to-Contract Bridge (D-CPMP-9)

When a proposal is marked `awarded`:
1. Portfolio Manager receives hook notification
2. Creates new `cpmp_contracts` record pre-populated from proposal data (title, agency, NAICS, contract type)
3. Seeds initial deliverables from compliance matrix CDRL items
4. Links `proposal_opportunities.contract_id = cpmp_contracts.id`
5. **Requires explicit human confirmation** — never automatic

### 3.7 Feature-Flag Isolation (D-CHILD-6)

```python
# Environment variable controls feature visibility
ICDEV_GOVCON_ENABLED = os.environ.get("ICDEV_GOVCON_ENABLED", "true").lower() == "true"

# Routes conditionally registered
if govcon_enabled:
    # Register /proposals, /govcon, /cpmp routes
    ...

# Nav bar conditionally shows links
{% if govcon_enabled %}
<li><a href="/proposals">Proposals</a></li>
<li><a href="/govcon">GovCon</a></li>
{% endif %}
```

**Child app exclusion** via three mechanisms:
- `PARENT_ONLY_DIRS`: Excludes `tools/govcon/` from child app scaffolding
- `PARENT_ONLY_COMMANDS`: Excludes GovCon slash commands
- `_strip_govcon_from_dashboard()`: Removes routes from child Flask app

---

## 4. Database Schema

### Proposal Tables (13)

| Table | Type | Purpose |
|-------|------|---------|
| `proposal_opportunities` | CRUD | Pre-award opportunities from SAM.gov or manual entry |
| `proposal_volumes` | CRUD | Volume structure (Technical, Management, Cost, etc.) |
| `proposal_sections` | CRUD | Individual RFP section responses with 14-step status |
| `proposal_section_dependencies` | CRUD | Cross-reference dependency tracking between sections |
| `proposal_compliance_matrix` | CRUD | L/M/N compliance mapping per requirement |
| `proposal_reviews` | CRUD | Color review workflow (Red, Yellow, Green reviews) |
| `proposal_review_findings` | CRUD | Detailed findings from reviews |
| `proposal_status_history` | Append-only | All status transitions (NIST AU-2) |
| `proposal_section_drafts` | Append-only | AI-generated drafts awaiting HITL approval |
| `proposal_knowledge_base` | CRUD | Reusable content blocks (11 categories × 10 domains) |
| `proposal_questions` | CRUD | Q&A tracking from RFP ambiguity analysis |
| `proposal_amendments` | CRUD | RFP amendment version tracking |
| `proposal_question_responses` | CRUD | Government responses to submitted questions |

### Append-Only Tables (NIST AU-2)

- `proposal_status_history` — Every status transition recorded immutably
- `proposal_section_drafts` — AI-generated drafts preserved for audit trail

---

## 5. Configuration

### args/govcon_config.yaml

Key sections relevant to Phase 62:

**Response Drafting (D365):**
```yaml
response_drafting:
  llm_function: proposal_drafting
  max_draft_length_words: 500
  include_evidence: true
  include_tool_references: true
  confidence_threshold: 0.70
  max_knowledge_blocks_per_draft: 3
  product_detection:
    enabled: true
    min_keyword_matches: 2
```

**Knowledge Base (D368):**
```yaml
knowledge_base:
  max_blocks_per_draft: 3
  keyword_search_top_k: 5
  track_usage: true
  categories:
    - capability_description
    - approach
    - staffing
    - tools_used
    - past_performance
    - differentiator
    - management_approach
    - transition_plan
    - product_overview
    - integrated_solution
    - customer_value
```

**Proposal Lifecycle:**
```yaml
proposal_lifecycle:
  status_pipeline:
    - draft
    - under_development
    - draft_complete
    - review_assigned
    - red_review
    - yellow_review
    - green_review
    - color_reviews_complete
    - final_review
    - final_approved
    - submitted
    - awarded
  review_types:
    - red
    - yellow
    - green
    - final
```

### args/llm_config.yaml

Two-tier routing for proposal drafting:
```yaml
two_tier:
  worker_functions:
    - proposal_drafting
  # qwen3 drafts compact structured response
  # Claude reviews for quality, compliance, tone
```

---

## 6. CLI Commands

```bash
# Response drafting
python tools/govcon/response_drafter.py --opportunity-id "opp-xxx" --json
python tools/govcon/response_drafter.py --section-id "sec-xxx" --json

# Knowledge base
python tools/govcon/knowledge_base.py --list --json
python tools/govcon/knowledge_base.py --add --category capability_description --domain devsecops --title "DevSecOps Pipeline" --content "..." --json
python tools/govcon/knowledge_base.py --search "zero trust" --json

# Compliance matrix
python tools/govcon/compliance_populator.py --opportunity-id "opp-xxx" --json

# Question generation
python tools/govcon/question_generator.py --opportunity-id "opp-xxx" --json
python tools/govcon/question_exporter.py --opportunity-id "opp-xxx" --format html --json

# Amendment tracking
python tools/govcon/amendment_tracker.py --opportunity-id "opp-xxx" --json

# Portfolio bridge
python tools/govcon/portfolio_manager.py --bridge --opportunity-id "opp-xxx" --json
python tools/govcon/portfolio_manager.py --health --json
```

---

## 7. Dashboard

### Pages

| Route | Page | Content |
|-------|------|---------|
| `/proposals` | Opportunity List | Stat grid, filtering, search, "New Opportunity" modal |
| `/proposals/<id>` | Opportunity Detail | 6 tabs: Overview, Sections, Compliance Matrix, Reviews, Timeline, Health |
| `/proposals/<id>/sections/<id>` | Section Detail | 14-step status pipeline, info grid, notes, compliance items, findings, dependencies, AI drafts |

### Dashboard Features

- **Stat Grid** — Total Opportunities, Active Proposals, Win Rate, Pipeline Value
- **New Opportunity Modal** — Title, agency, NAICS, due date, estimated value, description
- **Section Editor** — Status pipeline badges, compliance items linked to sections, AI draft with accept/reject/edit
- **Color Review Workflow** — Red/Yellow/Green/Final review cards with findings, score, reviewer
- **Timeline Gantt** — Visual timeline of section due dates and review milestones
- **Compliance Matrix** — L/M/N heatmap by section and control family

### Navigation

GovProposal links appear in the nav bar only when `ICDEV_GOVCON_ENABLED=true`:
```html
{% if govcon_enabled %}
<li><a href="/proposals">Proposals</a></li>
<li><a href="/govcon">GovCon</a></li>
{% endif %}
```

---

## 8. Architecture Decisions

| ADR | Decision |
|-----|----------|
| D365 | Two-tier LLM drafting: qwen3 worker compact draft (~400 words) → Claude reviewer polish. Template fallback for air-gap. Net context budget savings vs. Claude-only |
| D368 | Knowledge base with 11 categories × 10 domains. Usage tracking for quality-based selection. Keyword search with top-k retrieval |
| D-CPMP-9 | Explicit proposal-to-contract bridge — human confirms creation, pre-populates from proposal metadata, seeds deliverables from compliance matrix |
| D-CHILD-3 | `PARENT_ONLY_DIRS` list prevents GovProposal tools from leaking to child apps |
| D-CHILD-4 | Full dashboard in child apps strips GovProposal/CPMP/GovCon routes via `_strip_govcon_from_dashboard()` |
| D-CHILD-6 | Feature-flag isolation via `ICDEV_GOVCON_ENABLED` env var — child apps and non-govcon deployments exclude these modules cleanly |

---

## 9. Security Considerations

- All proposal data inherits CUI // SP-CTI classification markings
- Append-only tables (`proposal_status_history`, `proposal_section_drafts`) satisfy NIST AU-2 audit requirements
- AI-generated drafts require HITL approval before flowing to proposal sections — no auto-publish
- Two-tier LLM drafting preserves prompt/response hashes in AI telemetry (SHA-256, D216)
- Feature-flag isolation prevents GovProposal from being scaffolded into customer-facing child apps
- Knowledge base content blocks are CUI-marked at creation time
- SAM.gov API key stored in environment variable (`SAM_GOV_API_KEY`), never in code or config
- Compliance matrix grades are deterministic (capability score thresholds), not probabilistic

---

## 10. Integration Points

| Integration | Direction | Description |
|-------------|-----------|-------------|
| Phase 59 (GovCon Intelligence) | Upstream | SAM.gov opportunities, extracted requirements, capability scores feed proposal lifecycle |
| Phase 60 (CPMP) | Downstream | Won proposals bridge to contracts via portfolio manager |
| Phase 35 (Innovation Engine) | Bidirectional | Capability gaps (N-graded) register as innovation signals; solutions enhance capability catalog |
| Phase 58 (Creative Engine) | Bidirectional | Competitor profiles share with creative competitor tracking |
| Two-Tier LLM | Internal | qwen3 drafts, Claude reviews via `tools/llm/router.py` |
| Dashboard | Internal | 3 proposal pages + GovCon action bar integration |

---

## 11. Testing

Testing follows existing ICDEV™ patterns:

- Unit tests for deterministic functions (compliance grading, status transitions, knowledge base search)
- Integration tests for database operations (append-only enforcement, bridge creation)
- API tests for proposal CRUD and draft generation endpoints
- E2E tests for dashboard pages (Playwright MCP)

---

## 12. Content Guidance

- Management Volume responses must reference the **post-award management portal** (CPMP) that tracks requirements and CDRLs
- Technical Volume responses should reference specific ICDEV™ tools and capabilities from the knowledge base
- Past Performance responses should include CPARS-compatible rating language
- All proposal content must use Government-appropriate formal tone

**CUI // SP-CTI**
