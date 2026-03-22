# Proposal Genesis — Phases A–F: Autonomous Capture-to-Learning Pipeline

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | Proposal Genesis Phase A + B + C + D + E + F |
| Title | Autonomous Capture-to-Learning Pipeline + Competitive Intelligence + CRM Engagement + Pulse Integration + Post-Award Monitoring + Bid Decision & Win/Loss Learning |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 59 (GovCon Intelligence), Phase 62 (GovProposal RFX), Genesis v2.0 (Daemon Pattern) |
| Author | ICDEV™ Architect Agent |
| Date | 2026-03-14 |

---

## 1. Problem Statement

Phases 59 and 62 built manual GovCon Intelligence and proposal lifecycle management, but four gaps remained:

1. **No autonomous operation** — SAM.gov scanning, requirement extraction, capability mapping, response drafting, and quality checking all required manual triggering. No daemon continuously processed the pipeline.

2. **No quality gate** — Drafts were generated but never quality-checked. Grammar, readability, tone, plagiarism, and AI detection checks did not exist for proposal content.

3. **No bidirectional Pulse integration** — Pulse AI Blog Engine articles contained capability-relevant content that could strengthen proposals, and proposal capabilities could inform future articles. No integration existed.

4. **No Genesis pattern for GovCon** — Genesis v2.0 proved the autonomous reflex-based daemon pattern for research/development, but the same pattern had not been applied to the capture-to-delivery workflow.

Phase A closes these gaps by applying the Genesis v2.0 daemon pattern to create 5 autonomous reflexes forming the core capture pipeline: Discover → Extract → Map → Draft → Polish. Phase B adds competitive intelligence (Scout, Shape). Phase C adds CRM engagement tracking (Engage) — auto-creating accounts from opportunity agencies, logging interactions from audit events, and computing 4-dimension engagement scores. Phase D adds Pulse integration (Publish) — converting approved, quality-checked proposal drafts into case study articles staged in the Pulse AI Blog Engine. Phase E adds post-award monitoring (Monitor, Fulfill) — CPARS prediction with EVM early warnings, and CDRL auto-generation with compliance refresh. Phase F closes the learning loop with bid/no-bid decision scoring (Decide), win/loss analysis with lessons learned (Analyze), and approved draft → fine-tuning pair generation (Train).

---

## 2. Goals

1. Implement a single-daemon, multi-reflex autonomous pipeline for proposal capture
2. Create 5 Phase A reflexes: R1 Discover, R5 Extract, R6 Map, R7 Draft, R8 Polish
3. Implement deterministic quality gate (5 checks: grammar, readability, tone, plagiarism, AI detection)
4. Integrate Pulse AI Blog Engine content bidirectionally with proposal drafts (D-PG-5)
5. Provide pipeline chain auto-trigger (discover → extract → map → draft → polish)
6. Add circuit breakers (3 failures → open) and trust kernel with GREEN/YELLOW risk tiers
7. Create dashboard page with stat grid, reflex table, quality scores, and audit trail
8. Create API endpoints for status, reflex control, pipeline execution, and quality scores

---

## 3. Architecture

### 3.1 Genesis v2.0 Pattern Adaptation

Proposal Genesis reuses the Genesis v2.0 daemon pattern with domain-specific modifications:

| Genesis v2.0 | Proposal Genesis |
|--------------|------------------|
| 12 Reflexes (research lab) | 14 Reflexes across 4 phases (CAPTURE, PROPOSE, DELIVER, LEARN) |
| 3 risk tiers (GREEN/YELLOW/ORANGE) | 2 risk tiers (GREEN/YELLOW only, D-PG-8) |
| v2-genesis branch | main branch (D-PG-2) |
| Scanner-tier LLM only | Scanner-tier + two-tier for R7 Draft only (D-PG-7) |
| GKP knowledge export | Direct DB integration with GovCon tables |

### 3.2 Phase A Reflexes (Core Pipeline)

| Reflex | Name | Phase | Risk Tier | Function |
|--------|------|-------|-----------|----------|
| R1 | Discover | CAPTURE | GREEN | SAM.gov scanning + amendment tracking |
| R5 | Extract | PROPOSE | GREEN | Shall/must/should statement extraction from RFPs |
| R6 | Map | PROPOSE | GREEN | Capability mapping + partner/KB enrichment |
| R7 | Draft | PROPOSE | YELLOW | Two-tier LLM response drafting with Pulse content |
| R8 | Polish | PROPOSE | GREEN | 5-dimension quality gate (deterministic) |

### 3.3 Phase B Reflexes (Competitive Intelligence)

| Reflex | Name | Phase | Risk Tier | Schedule | Function |
|--------|------|-------|-----------|----------|----------|
| R2 | Scout | CAPTURE | GREEN | daily 07:00 | Competitor award tracking + intel briefs |
| R3 | Shape | CAPTURE | YELLOW | daily 08:00 | Capture planning + teaming assessment |

**R2 Scout** operates on an independent daily schedule (not part of the pipeline chain). Steps:
1. Scan SAM.gov for new award notices (air-gap safe — skips in `ICDEV_AIR_GAPPED` mode)
2. Build competitor leaderboard from `govcon_awards` table
3. Detect competitive overlaps with tracked opportunities (NAICS/agency matching)
4. Generate markdown intel brief (deterministic template, scanner-tier)
5. Store brief to `data/proposal_genesis/briefs/`

**R3 Shape** runs independently on a daily schedule. Steps:
1. Find tracked opportunities without capture plans → create skeleton plans
2. Derive deterministic win strategy from opportunity attributes (tech/compliance/DoD keyword matching)
3. Derive discriminators from opportunity context (7 keyword categories)
4. Get active teaming partners from `pg_teaming_partners`
5. Assess partner fit using 4-dimension scoring:
   - Capability keyword overlap (40% weight)
   - Certification relevance (20% weight)
   - Contract vehicle match (20% weight)
   - Set-aside alignment (20% weight)
6. Store assessments for at-least-marginal-fit partners
7. Update capture plan teaming strategy from assessment results

### 3.4 Phase C Reflexes (CRM Engagement)

| Reflex | Name | Phase | Risk Tier | Schedule | Function |
|--------|------|-------|-----------|----------|----------|
| R4 | Engage | CAPTURE | GREEN | every 4h | CRM account/contact tracking + engagement scoring |

**R4 Engage** operates on an independent 4-hour schedule. Steps:
1. Discover accounts from tracked opportunities — auto-create `pg_crm_accounts` from agency names
2. Classify account type (government vs other) via keyword matching
3. Log interactions from recent audit events (maps event types to CRM interaction types)
4. Compute 4-dimension engagement scores for all active accounts:
   - Interaction recency (30% weight): exponential decay over 90 days
   - Interaction frequency (25% weight): normalized count (cap at 10)
   - Opportunity pipeline (25% weight): normalized active opportunity count (cap at 5)
   - Win rate (20% weight): placeholder until win/loss data available (Phase F)

### 3.5 Phase D Reflexes (Pulse Integration)

| Reflex | Name | Phase | Risk Tier | Schedule | Function |
|--------|------|-------|-----------|----------|----------|
| R12 | Publish | DELIVER | YELLOW | on_demand | Approved proposal drafts → Pulse case study articles |

**R12 Publish** converts approved, quality-checked proposal drafts into case study articles staged in the Pulse AI Blog Engine. Steps:
1. Find approved drafts with quality scores >= 70 that have no existing Pulse link
2. Retrieve knowledge base blocks for enrichment (domain-matched, usage-ranked)
3. Generate deterministic case study article (template-based, zero LLM):
   - Extract capabilities from proposal content (33 keyword categories)
   - Build Challenge section from first 2-3 sentences of proposal
   - Build Approach section from middle portion + key technologies
   - Add Technical Depth from knowledge base blocks
   - Generate tags from domain, agency, NAICS
4. Stage article in `pulse_posts` as `draft` with `author_id='pg_publish'` (NEVER auto-publishes)
5. Create `pg_pulse_proposal_links` entry with `link_type='cdrl_to_case_study'` for bidirectional traceability (D-PG-5)
6. Audit all decisions (stage success/failure) to `pg_proposal_genesis_audit`

### 3.6 Phase E Reflexes (Post-Award Monitoring)

| Reflex | Name | Phase | Risk Tier | Schedule | Function |
|--------|------|-------|-----------|----------|----------|
| R10 | Monitor | DELIVER | GREEN | every 4h | CPARS prediction + EVM early warnings |
| R11 | Fulfill | DELIVER | YELLOW | daily 09:00 | CDRL auto-generation + compliance refresh |

**R10 Monitor** scans active CPMP contracts for health issues. Steps:
1. Get all active/option-pending contracts from `cpmp_contracts`
2. Retrieve latest EVM period data per contract (CPI, SPI, TCPI, EAC, BAC)
3. Assess EVM health using threshold-based scoring:
   - CPI/SPI < 0.80 = critical (-0.40), < 0.90 = warning (-0.20), < 0.95 = info (-0.10)
   - TCPI > 1.20 = warning (-0.10)
   - Score = max(0, 1.0 + sum(penalties))
4. Get overdue/upcoming deliverables from `cpmp_deliverables`
5. Assess schedule health (overdue count and severity, upcoming due count)
6. Get open negative events from `cpmp_negative_events`
7. Assess risk health (negative events weighted by severity: critical=0.40, high=0.30, medium=0.15, low=0.05)
8. Predict CPARS rating using deterministic weighted average (D-CPMP-3):
   - EVM health: 0.35 weight
   - Schedule health: 0.25 weight
   - Risk health: 0.20 weight
   - Small business compliance: 0.10 weight
   - Trend direction: 0.10 weight
9. Compute contract health score (D-CPMP-8): green >= 0.80, yellow 0.60-0.79, red < 0.60
10. Store monitoring results to `pg_proposal_genesis_audit`

**R11 Fulfill** auto-generates CDRLs for deliverables approaching due date. Steps:
1. Find deliverables due within 14 days with no prior generation
2. Resolve CDRL type from deliverable metadata (keyword matching on cdrl_number, title, deliverable_type)
3. Dispatch to mapped ICDEV™ generation tool via subprocess (D-CPMP-5):
   - `ssp` → `tools/compliance/ssp_generator.py`
   - `sbom` → `tools/compliance/sbom_generator.py`
   - `poam` → `tools/compliance/poam_generator.py`
   - `stig_checklist` → `tools/compliance/stig_checker.py`
   - `evm_report` → `tools/govcon/evm_engine.py`
   - `icd` → `tools/mosa/icd_generator.py`
   - `tsp` → `tools/mosa/tsp_generator.py`
   - `test_report` → `tools/testing/test_orchestrator.py`
   - `security_scan` → `tools/security/sast_runner.py`
4. Record generation results in `cpmp_cdrl_generations`
5. Update deliverable status to `in_progress` on success
6. Check for stale compliance documentation (> 90 days since last generation)
7. Flag stale docs for compliance refresh in audit trail

### 3.7 Phase F Reflexes (Learning + Decide)

| Reflex | Name | Phase | Risk Tier | Schedule | Function |
|--------|------|-------|-----------|----------|----------|
| R9 | Decide | PROPOSE | GREEN | on_demand | Bid/no-bid scoring + win probability estimation |
| R13 | Analyze | LEARN | GREEN | weekly Sun 20:00 | Win/loss analysis + lessons learned |
| R14 | Train | LEARN | YELLOW | weekly Sun 22:00 | Approved drafts → fine-tuning pairs → Ollama |

**R9 Decide** performs deterministic bid/no-bid scoring. Steps:
1. Find tracked opportunities with status `active`/`tracking` and no existing bid decision
2. Score each opportunity across 6 dimensions (deterministic weighted average, D21):
   - Capability fit (0.25): keyword overlap between opportunity description and ICDEV™ capability catalog
   - Past performance (0.20): win rate for same agency/NAICS from `pg_win_loss_records`
   - Competitive position (0.15): inverse competitor count from `govcon_awards` for same NAICS
   - Compliance readiness (0.15): IL + set-aside + security clearance match scoring
   - Resource availability (0.15): active draft count capacity check (< 10 active = 1.0)
   - Strategic alignment (0.10): keyword matching for strategic priority areas
3. Apply decision thresholds:
   - Score >= 0.60 → `bid` recommendation
   - Score < 0.35 → `no_bid` recommendation
   - Score 0.35–0.59 → `deferred` (needs human review)
4. Store decision with full score breakdown and rationale to `pg_bid_decisions`
5. Audit all decisions to `pg_proposal_genesis_audit`

**R13 Analyze** performs win/loss analysis on completed opportunities. Steps:
1. Find `pg_win_loss_records` without associated lessons (LEFT JOIN `pg_win_loss_lessons`)
2. For each unanalyzed record:
   - Categorize lessons using keyword-based classification across 7 categories:
     - `technical`: technology, architecture, engineering, integration, cloud, API, security
     - `management`: schedule, team, staffing, communication, leadership, risk, timeline
     - `pricing`: cost, price, budget, rate, discount, competitive pricing, LPTA, best value
     - `past_performance`: performance, CPARS, reference, track record, experience
     - `compliance`: compliance, NIST, FedRAMP, CMMC, STIG, ATO, security control
     - `staffing`: personnel, hiring, clearance, certification, resume, key personnel
     - `other`: fallback category
   - Extract lessons from `lessons_learned`, `our_strengths`, `our_weaknesses`, and `competitor_strengths` fields
   - Mark extracted lessons as actionable (default: true)
3. Store categorized lessons in `pg_win_loss_lessons`
4. Report total analyses completed and lessons extracted

**R14 Train** generates fine-tuning pairs from 3 source streams. Steps:
1. **Approved drafts** — Find `proposal_section_drafts` with status `approved` and no existing training pair:
   - Generate Q&A pairs using section title as question, draft body as answer
   - Prefix: `"Write a proposal section for: {title}"`
2. **Win/loss lessons** — Find `pg_win_loss_lessons` with `actionable=1` and no existing training pair:
   - Generate pairs from lesson text with category context
   - Prefix: `"What lesson was learned about {category} from {outcome}?"`
3. **Knowledge base** — Find `proposal_knowledge_base` entries with no existing training pair:
   - Generate pairs from content with domain/topic context
   - Prefix: `"What do you know about {topic} in the {domain} domain?"`
4. SHA-256 content hash dedup (D-RAG-5 pattern) — skip if hash already in `pg_training_pair_sources`
5. Store pairs in `pg_training_pair_sources` with source_type, source_id, content_hash
6. Report total pairs generated across all 3 streams

### 3.8 Pipeline Chain + Independent Reflexes

```
Pipeline chain:  Discover → Extract → Map → Draft → Polish
                    R1         R5       R6     R7       R8

Independent:     Scout (daily 07:00)    Shape (daily 08:00)    Engage (every 4h)
                   R2                     R3                     R4

Independent:     Monitor (every 4h)     Fulfill (daily 09:00)
                   R10                    R11

On-demand:       Publish (trigger: after_fulfill)    Decide (on_demand)
                   R12                                  R9

Weekly:          Analyze (Sun 20:00)    Train (Sun 22:00)
                   R13                    R14
```

Pipeline chain auto-triggers each step via `--pipeline`. R2 Scout, R3 Shape, R4 Engage, R10 Monitor, and R11 Fulfill run on independent schedules. R12 Publish and R9 Decide run on demand. R13 Analyze and R14 Train run on weekly schedules.

### 3.5 Reflex Contract

Every reflex implements:
```python
def run(config: Dict, trust: Any) -> Dict:
    return {
        "success": bool,
        "metric_value": float,  # 0.0-1.0
        "details": dict
    }
```

---

## 4. Database Schema

All tables use `pg_` prefix for namespace isolation from existing GovCon tables.

### Phase A Tables

| Table | Purpose | Append-only |
|-------|---------|-------------|
| `pg_proposal_genesis_audit` | Audit trail for all autonomous decisions | Yes (NIST AU-2) |
| `pg_amendment_diffs` | Amendment change diffs for re-extraction | Yes |
| `pg_pulse_proposal_links` | Bidirectional Pulse-proposal content links | Yes |
| `pg_proposal_quality_scores` | Quality check results (grammar, readability, tone, plagiarism, AI detection) | Yes |

### Phase B Tables

| Table | Purpose | Append-only |
|-------|---------|-------------|
| `pg_capture_plans` | Skeleton capture plans for tracked opportunities | No (status updates) |
| `pg_capture_activities` | Capture management activities per plan | Yes |
| `pg_teaming_partners` | Partner registry with capabilities, certs, vehicles | No (status updates) |
| `pg_teaming_assessments` | Partner fit assessments per opportunity | Yes |
| `govcon_awards` | Competitor award data for overlap detection | Yes |

### Phase C Tables

| Table | Purpose | Append-only |
|-------|---------|-------------|
| `pg_crm_accounts` | CRM accounts auto-created from opportunity agencies | No (status updates) |
| `pg_crm_contacts` | Contact registry per account | No (status updates) |
| `pg_crm_interactions` | CRM interaction log from audit events | Yes |
| `pg_crm_engagement_scores` | 4-dimension engagement scoring per account | Yes |

### Phase E Tables (existing CPMP tables, no new PG tables)

Phase E reflexes operate on existing CPMP tables created by Phase 60:

| Table | Used By | Purpose |
|-------|---------|---------|
| `cpmp_contracts` | R10 Monitor | Active contract list, health scoring |
| `cpmp_evm_periods` | R10 Monitor | EVM data (CPI, SPI, TCPI, EAC, BAC) |
| `cpmp_deliverables` | R10 Monitor, R11 Fulfill | Deliverable due dates, status, generation tracking |
| `cpmp_negative_events` | R10 Monitor | Open negative events for risk health |
| `cpmp_cpars_assessments` | R10 Monitor | Current CPARS ratings |
| `cpmp_small_business_plan` | R10 Monitor | Small business compliance data |
| `cpmp_cdrl_generations` | R11 Fulfill | CDRL generation records (append-only) |

### Phase F Tables

| Table | Purpose | Append-only |
|-------|---------|-------------|
| `pg_bid_decisions` | Bid/no-bid decision records with score breakdown | Yes |
| `pg_bid_decision_outcomes` | Decision outcome tracking (won/lost/no_award/cancelled/withdrawn) | Yes |
| `pg_win_loss_records` | Win/loss calibration data | Yes |
| `pg_win_loss_lessons` | Categorized lessons learned from outcomes | Yes |
| `pg_training_pair_sources` | Fine-tuning training pairs from 3 source streams | Yes |

---

## 5. Configuration

**File:** `args/proposal_genesis_config.yaml`

Key settings:
- `master_switch.enabled` — Global toggle (env override: `ICDEV_PROPOSAL_GENESIS_ENABLED`)
- `reflexes.<name>.enabled` — Per-reflex toggle
- `reflexes.<name>.risk_tier` — GREEN or YELLOW
- `reflexes.<name>.schedule_minutes` — Auto-run interval (daemon mode)
- `trust_kernel.yellow_requires_review` — Whether YELLOW tier needs human review
- `circuit_breaker.failure_threshold` — Failures before tripping (default: 3)

---

## 6. CLI Commands

```bash
# Daemon
ICDEV_PROPOSAL_GENESIS_ENABLED=true python tools/proposal_genesis/daemon.py
python tools/proposal_genesis/daemon.py --once --json
python tools/proposal_genesis/daemon.py --status --json
python tools/proposal_genesis/daemon.py --reflex discover --json
python tools/proposal_genesis/daemon.py --pipeline --json
python tools/proposal_genesis/daemon.py --enable discover
python tools/proposal_genesis/daemon.py --disable draft
python tools/proposal_genesis/daemon.py --reset polish --json
```

---

## 7. Dashboard

**Route:** `/proposal-genesis`
**Template:** `tools/dashboard/templates/proposal_genesis.html`

### Sections
1. **Intro Panel** — 10-step pipeline visualization (Discover → Extract → Map & Draft → Quality Check → Scout & Shape → CRM Engage → Monitor & Fulfill → Pulse Publish → Bid Decide → Win/Loss Learn)
2. **Stat Grid** — 22 cards: Daemon Status, Active Opportunities, Shall Statements, Pending Drafts, Avg Quality Score, Pulse Links, Capture Plans, Teaming Assessments, CRM Accounts, CRM Interactions, Avg Engagement, Published Articles, Case Study Links, Active Contracts, At-Risk Contracts, Overdue Deliverables, CDRLs Generated, Bid Decisions, Bid Recommendations, Win/Loss Records, Win/Loss Lessons, Training Pairs
3. **Reflexes Table (Phases A–F)** — All 14 reflexes with phase, tier badges, status, run counts, last metric, Run buttons
4. **Capture Plans (R3 Shape)** — Opportunity, agency, NAICS, status, win strategy, teaming strategy, last updated
5. **Teaming Assessments (R3 Shape)** — Opportunity, partner, fit score, recommendation, gaps filled, risks
6. **CRM Accounts (R4 Engage)** — Account, agency, type, status, engagement score, last updated
7. **CRM Interactions (R4 Engage)** — Account, type, subject, opportunity, date
8. **Engagement Scores (R4 Engage)** — Account, composite score, recency, frequency, pipeline, win rate, created
9. **Published Articles (R12 Publish)** — Title, status, domain, readability score, created
10. **Case Study Links (CDRL → Pulse)** — Opportunity, Pulse post ID, section ID, link type, relevance score, created
11. **Contract Health (R10 Monitor)** — Contract #, title, agency, health badge, score, CPARS rating, value, PoP end
12. **Overdue Deliverables (R10 Monitor)** — Contract, CDRL #, title, type, due date, days overdue
13. **CDRL Generations (R11 Fulfill)** — Contract, CDRL #, deliverable, type, tool, status, generated date
14. **Quality Scores (R8 Polish)** — Composite + 5 individual scores per draft
15. **Bid Decisions (R9 Decide)** — Opportunity, decision badge, win probability, score breakdown, rationale, date
16. **Win/Loss Records (R13 Analyze)** — Opportunity, outcome badge, competitor, strengths, weaknesses, lessons
17. **Win/Loss Lessons (R13 Analyze)** — Category badge, lesson text, actionable flag, applied status
18. **Training Pairs (R14 Train)** — Source type, prompt preview, response preview, content hash, date
19. **Audit Trail** — Timestamped reflex actions with opportunity context
20. **Reflex Output** — JSON output panel for interactive reflex runs

### API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/proposal-genesis/status` | Daemon status with reflex details |
| POST | `/api/proposal-genesis/reflex/<name>` | Run a single reflex |
| POST | `/api/proposal-genesis/pipeline` | Run full pipeline chain |
| GET | `/api/proposal-genesis/quality-scores` | Recent quality check results |
| GET | `/api/proposal-genesis/audit` | Audit trail events |
| GET | `/api/proposal-genesis/pulse-links` | Pulse-proposal content links |
| GET | `/api/proposal-genesis/summary` | High-level dashboard metrics (incl. Phase B + C stats) |
| GET | `/api/proposal-genesis/capture-plans` | Capture plans with opportunity context (Phase B) |
| GET | `/api/proposal-genesis/teaming-assessments` | Partner fit assessments (Phase B) |
| GET | `/api/proposal-genesis/crm-accounts` | CRM accounts with engagement scores (Phase C) |
| GET | `/api/proposal-genesis/crm-interactions` | Recent CRM interactions (Phase C) |
| GET | `/api/proposal-genesis/engagement-scores` | Engagement scores per account (Phase C) |
| GET | `/api/proposal-genesis/published-articles` | Case study articles staged by Publish reflex (Phase D) |
| GET | `/api/proposal-genesis/case-study-links` | CDRL-to-case-study Pulse links (Phase D) |
| GET | `/api/proposal-genesis/contract-health` | Contract health with EVM/CPARS data (Phase E) |
| GET | `/api/proposal-genesis/cpars-predictions` | CPARS prediction scores per contract (Phase E) |
| GET | `/api/proposal-genesis/overdue-deliverables` | Overdue deliverables across active contracts (Phase E) |
| GET | `/api/proposal-genesis/cdrl-generations` | CDRL generation history with tool/status (Phase E) |
| GET | `/api/proposal-genesis/bid-decisions` | Bid/no-bid decisions with score breakdown (Phase F) |
| GET | `/api/proposal-genesis/win-loss-records` | Win/loss records for analysis (Phase F) |
| GET | `/api/proposal-genesis/win-loss-lessons` | Categorized lessons learned (Phase F) |
| GET | `/api/proposal-genesis/training-pairs` | Fine-tuning training pairs from 3 streams (Phase F) |

---

## 8. Architecture Decisions

| ADR | Decision |
|-----|----------|
| D-PG-1 | Separate daemon from Genesis v2.0 (different domain, different lifecycle) |
| D-PG-2 | Lives on main branch (not experimental — production GovCon tool) |
| D-PG-3 | Toggle via `ICDEV_PROPOSAL_GENESIS_ENABLED` env var (default: false) |
| D-PG-4 | Knowledge Base is never stale — historical data / past performance is permanent |
| D-PG-5 | Bidirectional Pulse integration (articles → proposals AND capabilities → articles) |
| D-PG-6 | Expanded capability catalog beyond ICDEV™ (consulting, domain expertise, partners) |
| D-PG-7 | Scanner-tier LLM for most reflexes; two-tier for R7 Draft only |
| D-PG-8 | No ORANGE risk tier — human review at pipeline end only (not per-reflex) |
| D-PG-9 | Win/loss calibration feeds back into scoring (Phase F) |
| D-PG-10 | Lightweight CRM for teaming/engagement tracking (Phase C) |
| D-PG-11 | R2 Scout generates deterministic markdown briefs (no LLM, scanner-tier template) |
| D-PG-12 | R2 Scout air-gap safe — skips SAM.gov scanning when `ICDEV_AIR_GAPPED=true` |
| D-PG-13 | Competitive overlap detection uses NAICS + agency keyword matching against `govcon_awards` |
| D-PG-14 | R3 Shape creates skeleton capture plans using deterministic template-based generation (no LLM) |
| D-PG-15 | Teaming assessment uses 4-dimension deterministic scoring (capability 0.40, certs 0.20, vehicles 0.20, set-asides 0.20) |
| D-PG-16 | Only store assessments for at-least-marginal fit partners (score >= 0.30) |
| D-PG-17 | Win strategy derivation uses keyword matching against tech/compliance/DoD vocabularies |
| D-PG-18 | R2 Scout and R3 Shape run on independent daily schedules, not part of pipeline chain |
| D-PG-19 | R4 Engage auto-creates CRM accounts from opportunity agencies (deterministic keyword classification) |
| D-PG-20 | Engagement scoring uses 4-dimension deterministic weighted average (recency 0.30, frequency 0.25, pipeline 0.25, win_rate 0.20) |
| D-PG-21 | R4 Engage runs independently every 4h, not part of pipeline chain (same pattern as R2/R3) |
| D-PG-22 | Interaction logging maps audit event types to CRM interaction types (deterministic mapping, scanner-tier) |
| D-PG-23 | R12 Publish uses deterministic template-based case study generation (zero LLM tokens, air-gap safe) |
| D-PG-24 | R12 Publish stages articles as `draft` with `author_id='pg_publish'` — NEVER auto-publishes (D-GEN principle) |
| D-PG-25 | R12 Publish creates `pulse_posts` table if it doesn't exist (graceful degradation when Pulse module not initialized) |
| D-PG-26 | Case study traceability via `pg_pulse_proposal_links` with `link_type='cdrl_to_case_study'` (D-PG-5 bidirectional integration) |
| D-PG-27 | Publishable draft criteria: status='approved' AND (quality_score >= 70 OR NULL) AND no existing Pulse link |
| D-PG-28 | R10 Monitor uses deterministic CPARS prediction (D-CPMP-3 weighted average) — EVM 0.35, schedule 0.25, risk 0.20, small_biz 0.10, trend 0.10 |
| D-PG-29 | R10 Monitor EVM health uses threshold-based scoring with CPI/SPI < 0.80 as critical, < 0.90 as warning, < 0.95 as info |
| D-PG-30 | R10 Monitor contract health uses D-CPMP-8 formula — green >= 0.80, yellow 0.60-0.79, red < 0.60 |
| D-PG-31 | R11 Fulfill dispatches to existing ICDEV™ tools via subprocess (D-CPMP-5) — 9 CDRL types mapped to generation scripts |
| D-PG-32 | R11 Fulfill flags stale compliance docs (> 90 days) for refresh — configurable via `stale_threshold_days` |
| D-PG-33 | R10 Monitor and R11 Fulfill run on independent schedules (every 4h / daily 09:00), not part of pipeline chain |
| D-PG-34 | R11 Fulfill uses `stdin=subprocess.DEVNULL` and 300s timeout for safe subprocess execution |
| D-PG-35 | R9 Decide uses 6-dimension deterministic weighted average (D21): capability_fit 0.25, past_performance 0.20, competitive_position 0.15, compliance_readiness 0.15, resource_availability 0.15, strategic_alignment 0.10 |
| D-PG-36 | R9 Decide thresholds: bid >= 0.60, no_bid < 0.35, deferred 0.35–0.59 (human review zone) |
| D-PG-37 | R13 Analyze uses keyword-based lesson categorization across 7 categories (no LLM, scanner-tier, air-gap safe) |
| D-PG-38 | R14 Train generates pairs from 3 source streams: approved drafts, win/loss lessons, knowledge base (D-FT-10 pattern) |
| D-PG-39 | R14 Train uses SHA-256 content hash dedup (D-RAG-5 pattern) — never generates duplicate training pairs |
| D-PG-40 | R14 Train is YELLOW tier — writes to training pair sources table (reversible) |
| D-PG-41 | R13 Analyze and R14 Train run weekly (Sun 20:00 and 22:00) — batch processing, not real-time |
| D-PG-42 | R9 Decide runs on_demand (after discover pipeline) — decisions are pipeline-triggered, not scheduled |

---

## 9. Quality Gate (R8 Polish)

5 deterministic checks (scanner-tier, zero LLM tokens):

| Check | Weight | Method |
|-------|--------|--------|
| Grammar | 0.20 | Regex: double spaces, uncapitalized sentences, repeated words, missing punctuation |
| Readability | 0.25 | Flesch-Kincaid grade level (ideal: 10-14 for proposals) |
| Tone | 0.25 | Keyword matching: informal language (-0.1), weak language (-0.1), strong proposal language (+0.03) |
| Plagiarism | 0.15 | 4-gram Jaccard similarity against other drafts |
| AI Detection | 0.15 | Sentence length variance (burstiness) proxy |

**Quality threshold:** 0.65 (configurable)

---

## 10. Testing

**Test file:** `tests/test_proposal_genesis.py` (173 tests)

| Test Class | Tests | Coverage |
|------------|-------|----------|
| TestDaemonModule | 7 | ReflexState, TrustKernel, module constants |
| TestPolishReflex | 10 | All 5 quality checks + composite scoring + storage |
| TestDraftReflex | 4 | Pulse keyword extraction + overlap scoring |
| TestDiscoverReflex | 4 | Air-gap detection + amendment storage |
| TestExtractReflex | 1 | Run function contract |
| TestMapReflex | 1 | Run function contract |
| TestProposalGenesisAPI | 11 | All API endpoints incl. Phase B (capture plans, teaming, summary stats) |
| TestScoutReflex | 8 | Air-gap detection, brief generation (data/air-gapped/error), overlap detection, run contract |
| TestShapeReflex | 10 | Win strategy derivation (tech/compliance/DoD/default), discriminators, partner fit scoring (strong/marginal/no-caps), capture plan creation, teaming assessment |
| TestEngageReflex | 15 | Account classification, event mapping, recency scoring, account creation (new/existing/no-agency), interaction logging, account lookup, full run contract |
| TestProposalGenesisPhaseCAPI | 8 | CRM accounts (with/without data), interactions, engagement scores, summary Phase C stats |
| TestDaemonPhaseC | 2 | PHASE_C_REFLEXES constant, engage in default config |
| TestPublishReflex | 13 | Case study generation, capability extraction, title sanitization, slugification, article staging, Pulse link creation, audit logging, challenge/approach extraction |
| TestProposalGenesisPhaseD | 5 | Published articles (empty/with data), case study links (empty/with data), summary Phase D stats |
| TestDaemonPhaseD | 3 | PHASE_D_REFLEXES constant, publish in reflex names, publish in config |
| TestMonitorReflex | 12 | EVM health (critical/warning/info/healthy), schedule health, risk health, CPARS prediction, contract health, run contract (with/without contracts) |
| TestFulfillReflex | 5 | CDRL type resolution (8 test cases), tool mapping, run with no deliverables, generate with missing tool |
| TestDaemonPhaseE | 3 | PHASE_E_REFLEXES constant, monitor/fulfill in reflex names, config validation |
| TestProposalGenesisPhaseEAPI | 7 | Contract health (empty/with data), overdue deliverables, CDRL generations, CPARS predictions, summary Phase E stats |
| TestDaemonPhaseF | 3 | PHASE_F_REFLEXES constant, decide/analyze/train in reflex names, config validation |
| TestDecideReflex | 5 | 6-dimension scoring, bid/no-bid/deferred thresholds, decision storage, run contract |
| TestAnalyzeReflex | 7 | Keyword categorization (7 categories), lesson extraction from strengths/weaknesses/competitor, multi-lesson analysis, run contract |
| TestTrainReflex | 8 | 3 source streams (drafts/lessons/KB), SHA-256 dedup, content hash generation, pair storage, run contract |
| TestProposalGenesisPhaseF_API | 10 | Bid decisions (empty/with data), win/loss records, win/loss lessons, training pairs, summary Phase F stats |

**E2E spec:** `.claude/commands/e2e/proposal_genesis.md`

---

## 11. Security Considerations

- All audit trail tables are append-only (NIST AU-2 compliant)
- 17 tables added to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py` (12 Phase A–E + 5 Phase F)
- Subprocess execution for daemon CLI uses `capture_output=True` with timeout
- Reflex allowlist prevents arbitrary command injection via API
- Quality scores stored with full check details for auditability
- Feature-flag isolation prevents accidental activation

---

## 12. Implementation Phases

| Phase | Reflexes | Status |
|-------|----------|--------|
| **A** (Core Pipeline) | R1 Discover, R5 Extract, R6 Map, R7 Draft, R8 Polish | **Implemented** |
| **B** (Competitive Intel) | R2 Scout, R3 Shape | **Implemented** |
| **C** (CRM) | R4 Engage | **Implemented** |
| **D** (Pulse Integration) | R12 Publish | **Implemented** |
| **E** (Post-Award) | R10 Monitor, R11 Fulfill | **Implemented** |
| **F** (Learning + Decide) | R9 Decide, R13 Analyze, R14 Train | **Implemented** |
