# Proposal Genesis — Autonomous Proposal Intelligence

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Proposal Genesis — Autonomous Proposal Intelligence

### Core Engine

| Tool | Path | Purpose |
|------|------|---------|
| daemon | `tools/proposal_genesis/daemon.py` | Autonomous proposal intelligence daemon: 14 Reflexes across 4 phases (CAPTURE, PROPOSE, DELIVER, LEARN). Subclass of DaemonBase (D-PG-1) |

### 19 Reflexes (tools/proposal_genesis/reflexes/)

| Reflex | Phase | Risk Tier | Purpose |
|--------|-------|-----------|---------|
| discover | CAPTURE | GREEN | Scan SAM.gov, internal signals for new opportunities |
| scout | CAPTURE | GREEN | Competitive intelligence and market analysis |
| shape | CAPTURE | GREEN | Win strategy, discriminators, partner fit assessment |
| engage | CAPTURE | GREEN | CRM account/contact/engagement tracking |
| extract | PROPOSE | GREEN | Extract requirements from opportunity documents |
| map | PROPOSE | GREEN | Map requirements to ICDEV™ capabilities |
| draft | PROPOSE | GREEN | Generate proposal section drafts |
| polish | PROPOSE | GREEN | Grammar, readability, tone, AI detection quality checks |
| decide | PROPOSE | YELLOW | Bid/no-bid decision with scoring |
| review | PROPOSE | GREEN | AI Color Team Review Simulator (Shipley color teams: Pink/Red/Gold/White/Black/Green) |
| trace | PROPOSE | GREEN | Compliance traceability — bidirectional L/M/C to proposal section mapping |
| monitor | DELIVER | GREEN | Track awarded contract performance |
| fulfill | DELIVER | GREEN | CDRL delivery tracking |
| publish | DELIVER | GREEN | Knowledge base article generation from wins |
| team | DELIVER | GREEN | Teaming partner scoring, workshare tracking, TA lifecycle management |
| bridge | DELIVER | GREEN | Proposal-to-Program Knowledge Bridge — transition packages on bid win |
| analyze | LEARN | GREEN | Win/loss analysis, lesson extraction |
| train | LEARN | GREEN | Generate fine-tuning pairs from approved content |
| adapt | LEARN | GREEN | Lightweight Shipley Process Adaptation — team size + complexity → process recommendation |

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Discover Reflex | tools/proposal_genesis/reflexes/discover.py | R01: Scan SAM.gov and internal signals for new opportunities | --json | Opportunity list |
| Scout Reflex | tools/proposal_genesis/reflexes/scout.py | R02: Competitive intelligence briefs for GovCon. Wraps govcon/award_tracker.py + competitor_profiler.py. Steps: scan SAM.gov awards, match awards to bid decisions → record outcomes + win/loss records (R13 feed), build competitor leaderboard, detect competitive overlaps with active opportunities, generate markdown intel brief. Scanner-tier only (zero Claude tokens). Briefs stored to data/proposal_genesis/briefs/. | run(config, trust) | {success, metric_value, details{briefs_generated, brief_path, new_awards, competitors_tracked, overlaps_found, outcomes_recorded, win_loss_created, air_gapped}} |
| Shape Reflex | tools/proposal_genesis/reflexes/shape.py | R03: Win strategy, discriminators, partner fit assessment | --json | Strategy recommendations |
| Engage Reflex | tools/proposal_genesis/reflexes/engage.py | R04: CRM account/contact/engagement tracking | --json | Engagement records |
| Extract Reflex | tools/proposal_genesis/reflexes/extract.py | R05: Shall statement mining + amendment re-extraction. Wraps govcon/requirement_extractor.py. Scanner-tier only (zero Claude tokens). Re-extracts from pg_amendment_diffs where re_extracted=0 | run(config, trust) | {success, metric_value, details{opportunities_processed, total_statements_extracted, amendment_re_extractions}} |
| Map Reflex | tools/proposal_genesis/reflexes/map.py | R06: Expanded capability matching — wraps govcon/capability_mapper.py with ICDEV™ + consulting + partner catalog (D-PG-6). GraphRAG/KARL enrichment for compliance-neighborhood discovery (§3.4, D-KARL-1/3). Scanner-tier only (zero Claude tokens). | run(config, trust) | {success, metric_value, details{opportunities_mapped, avg_coverage_score, partner_capabilities_available, graph_discovered_capabilities, mapping_results}} |
| Draft Reflex | tools/proposal_genesis/reflexes/draft.py | R07: Generate proposal section drafts | --json | Draft sections |
| Polish Reflex | tools/proposal_genesis/reflexes/polish.py | R08: Grammar, readability, tone, AI detection quality checks | --json | Quality report |
| Decide Reflex | tools/proposal_genesis/reflexes/decide.py | R09: Bid/no-bid decision with scoring | --json | Decision + score |
| Review Reflex | tools/proposal_genesis/reflexes/review.py | R15: AI Color Team Review Simulator — simulates Shipley color team reviews using deterministic scoring | --json | Review report |
| Trace Reflex | tools/proposal_genesis/reflexes/trace.py | R22: Compliance traceability — bidirectional L/M/C to proposal section mapping. Computes coverage metrics per opportunity, detects unmapped sections and amendment drift. Scanner-tier only (zero Claude tokens — fully deterministic). GREEN tier (read-only analysis). | run(config, trust) | {success, metric_value, details{opportunities_traced, avg_coverage_pct, trace_results}} |
| Monitor Reflex | tools/proposal_genesis/reflexes/monitor.py | R10: Track awarded contract performance | --json | Performance metrics |
| Fulfill Reflex | tools/proposal_genesis/reflexes/fulfill.py | R11: CDRL delivery tracking | --json | Delivery status |
| Publish Reflex | tools/proposal_genesis/reflexes/publish.py | R12: Proposal content → Pulse case study generation. Scans approved/quality-checked proposal drafts (status='approved', quality≥70), generates Pulse case study articles via deterministic template (scanner-tier, zero Claude tokens), stages in pulse_posts as 'draft' (NEVER auto-publishes, D-GEN), and creates pg_pulse_proposal_links for bidirectional traceability (D-PG-5). YELLOW tier (reversible writes). Integrates PulseSanitizer (Phase 70) to redact agency/program names before staging. | run(config, trust) | {success, metric_value, details{drafts_found, articles_staged, links_created, errors}} |
| Team Reflex | tools/proposal_genesis/reflexes/team.py | R23: Teaming partner scoring, workshare tracking, TA lifecycle management | --json | Teaming recommendations |
| Bridge Reflex | tools/proposal_genesis/reflexes/bridge.py | R24: Proposal-to-Program Knowledge Bridge — auto-generates transition packages on bid win | --json | Transition package |
| Analyze Reflex | tools/proposal_genesis/reflexes/analyze.py | R13: Win/loss analysis, lesson extraction | --json | Analysis report |
| Train Reflex | tools/proposal_genesis/reflexes/train.py | R14: Generate fine-tuning pairs from approved content | --json | Training pairs |
| Adapt Reflex | tools/proposal_genesis/reflexes/adapt.py | R21: Lightweight Shipley Process Adaptation — team size + opportunity complexity → process recommendation | --json | Adaptation plan |

