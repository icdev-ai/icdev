# CUI // SP-CTI
# Goal: GovCon Intelligence — Capture-to-Delivery Flywheel (Phase 59)

## Overview

Automate the government contracting capture pipeline from SAM.gov opportunity discovery through proposal response drafting. The flywheel:

```
SAM.gov RFPs → Mine requirement patterns → Map to ICDEV capabilities → Identify gaps →
Build enhancements → Draft proposals via GovProposal → Win → Deliver ICDEV free on-prem → Repeat
```

## Pipeline Stages

### 1. DISCOVER
**Tool:** `tools/govcon/sam_scanner.py`, `tools/govcon/award_tracker.py`
- Scan SAM.gov Opportunities API for new solicitations, pre-solicitations, RFIs
- Scan award notices for competitive intelligence
- Filter by NAICS codes (541512, 541511, 541519, 518210, 541513, 541330, 541611, 541690)
- Store in `sam_gov_opportunities` and `govcon_awards` tables
- Rate limit: 10 req/sec, 10K/day (D370)

### 2. EXTRACT
**Tool:** `tools/govcon/requirement_extractor.py`
- Extract "shall/must/will" statements from opportunity descriptions (D362 — deterministic regex)
- Domain-classify into 9 categories: devsecops, ai_ml, ato_rmf, cloud, security, compliance, agile, data, management
- Cluster by keyword fingerprint overlap (D364 — union-find)
- Store in `rfp_shall_statements` and `rfp_requirement_patterns`

### 3. MAP
**Tool:** `tools/govcon/capability_mapper.py`, `tools/govcon/gap_analyzer.py`
- Match requirement patterns to ICDEV capability catalog (`context/govcon/icdev_capability_catalog.json`)
- Compute coverage scores using keyword overlap
- Grade: L (>= 0.80), M (0.40-0.79), N (< 0.40)
- Identify gaps and generate enhancement recommendations
- Cross-register high-priority gaps to Innovation Engine (D361)

### 4. DRAFT
**Tool:** `tools/govcon/response_drafter.py`, `tools/govcon/compliance_populator.py`
- Two-tier LLM: qwen3 drafts compact response, Claude reviews and polishes (D365)
- Include capability evidence, tool references, compliance controls from knowledge base
- Store in `proposal_section_drafts` with status='draft'
- Human reviews → approves → content flows to `proposal_sections`
- Auto-populate L/M/N compliance matrix in `proposal_compliance_matrix`

## Supporting Tools

| Tool | Purpose |
|------|---------|
| `tools/govcon/knowledge_base.py` | Reusable content blocks by category, domain, NAICS. Keyword search. Usage tracking. |
| `tools/govcon/competitor_profiler.py` | Vendor profiling from award data. Leaderboard. Side-by-side comparison. |
| `tools/govcon/govcon_engine.py` | Pipeline orchestrator. Daemon mode with quiet hours. Status and reporting. |
| `tools/dashboard/api/govcon.py` | Flask Blueprint with 20+ REST endpoints bridging govcon tools into dashboard + proposal lifecycle. |

## Dashboard Pages

| Page | URL | Purpose |
|------|-----|---------|
| Pipeline | `/govcon` | SAM.gov scan status, recent opportunities, domain distribution, pipeline controls |
| Requirements | `/govcon/requirements` | Pattern frequency, domain heatmap, statement types, trend analysis |
| Capabilities | `/govcon/capabilities` | L/M/N coverage by domain, gap list, enhancement recommendations |
| Proposal Detail | `/proposals/<id>` | GovCon Intelligence action bar (Extract, Map, Compliance, Draft, Bid Rec) + AI Drafts tab |

## Integration Points

### GovProposal Integration
- SAM.gov opportunities → `proposal_opportunities` (via Import button or API)
- Shall statements → `proposal_compliance_matrix` (via Auto-Compliance)
- AI drafts → `proposal_sections` (via Approve workflow)
- Coverage scores → `proposal_compliance_matrix` compliance_status (L→compliant, M→partial, N→non_compliant)

### Innovation Engine Cross-Registration (D361)
- GovCon requirement patterns registered as `innovation_signals` with `category='govcon_opportunity'`
- Enables trend detection ("is cATO appearing more in RFPs?")
- Source adapter: `scan_sam_gov_for_innovation()` in `web_scanner.py`

### Creative Engine Cross-Registration (D361)
- Award leaderboard data registered as `creative_signals` with `source_type='sam_gov_rfp'`
- Enables competitive gap analysis against government contractors
- Source adapter: `scan_sam_gov_for_creative()` in `source_scanner.py`

## Business Model (D372)

| Model | Description |
|-------|-------------|
| `on_prem_free` | Winning customers get ICDEV free on-prem |
| `saas_paid` | Other bidders pay SaaS pricing |
| `negotiated` | Custom terms |
| `tbd` | Not yet decided |

### Customer Delivery Tiers (D374)
- **Core** (8 modules): SDLC essentials — builder, security, testing, compliance, CI/CD, dashboard
- **Standard** (12 modules): Full DevSecOps — adds ZTA, RICOAS intake, infrastructure, monitoring
- **Enterprise** (18 modules): Complete engineering suite — adds MBSE, modernization, gateway, multi-agent

### Internal-Only Components
These are NEVER included in customer delivery:
- govcon, creative, innovation, govproposal, saas, marketplace_admin

## Configuration

- `args/govcon_config.yaml` — SAM.gov API, extraction rules, capability mapping, drafting, business model, scheduling
- `context/govcon/icdev_capability_catalog.json` — Declarative capability catalog (~30 entries)

## Architecture Decisions

| ID | Decision |
|----|----------|
| D361 | SAM.gov scanner cross-registered in Innovation + Creative engines |
| D362 | Deterministic regex for shall extraction (air-gap safe) |
| D363 | Declarative JSON capability catalog |
| D364 | Keyword fingerprint clustering (union-find) |
| D365 | Two-tier LLM: qwen3 drafts, Claude reviews |
| D366 | SAM.gov API key via env var |
| D367 | Separate govcon_awards table for award data |
| D368 | Knowledge base in DB for searchability |
| D369 | GovCon tools in tools/govcon/ (separate domain) |
| D370 | Rate limiting: 10 req/sec, 10K/day |
| D371 | Patterns are append-only time-series |
| D372 | licensing_model field on proposal_opportunities |
| D373 | Drafts stored append-only, HITL gate before section |
| D374 | Customer delivery tier → deployment profile mapping |

## Verification

```bash
# Pipeline
python tools/govcon/govcon_engine.py --run --json
python tools/govcon/govcon_engine.py --status --json
python tools/govcon/govcon_engine.py --pipeline-report --json

# Individual stages
python tools/govcon/sam_scanner.py --scan --json
python tools/govcon/requirement_extractor.py --extract-all --json
python tools/govcon/requirement_extractor.py --patterns --min-frequency 3 --json
python tools/govcon/capability_mapper.py --map-all --json
python tools/govcon/gap_analyzer.py --analyze --json

# Dashboard
# Start: python tools/dashboard/app.py
# Navigate: /govcon, /govcon/requirements, /govcon/capabilities
# Navigate: /proposals/<id> → GovCon Intelligence action bar
```

## Edge Cases

- SAM.gov API key not set → graceful degradation, scan returns empty with error message
- No opportunities found for configured NAICS → returns empty result with suggestion to broaden NAICS
- LLM provider unavailable → drafting stage skipped, compliance population still works (deterministic)
- Quiet hours → daemon skips pipeline run, resumes after quiet window
- Duplicate opportunities → deduplication by solicitation_number + content_hash
