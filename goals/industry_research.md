# Industry Research Engine — Vertical Market Intelligence Pipeline

CUI // SP-CTI

## Purpose

The Industry Research Engine enables ICDEV™ to perform deep, structured research into target industry verticals before generating child applications. Instead of building blind, ICDEV™:
1. Scopes a target vertical (e.g., trading, healthcare, defense, fintech, cybersecurity, logistics)
2. Scans 8 data streams for challenges, regulations, community pain points, and competitive landscape
3. Scores and ranks industry challenges through a 6-dimension weighted model
4. Synthesizes findings into a structured dossier with build-vs-buy recommendations
5. Registers high-value findings to Innovation + Creative engines for cross-pollination
6. Feeds approved dossiers into the agentic child app fitness assessment pipeline

## Architecture Decision Records

- **D-RES-1:** Session-based research lifecycle — each research engagement tracked as a session with 8 states (created, scoping, scanning, synthesizing, dossier_ready, reviewed, child_app_triggered, archived). Append-only state transitions (D6 pattern)
- **D-RES-2:** 8 data streams scanned independently — community forums, review sites, academic papers, regulatory bodies, open-source projects, SaaS/commercial products, news/blogs, patents. Per-stream config in `args/research_config.yaml` (D26 pattern)
- **D-RES-3:** 6-dimension challenge scoring uses deterministic weighted average (D21 pattern) — reproducible, not probabilistic
- **D-RES-4:** Pipeline stages are idempotent — re-running a stage overwrites that stage's output without corrupting other stages. Partial runs resumable via session state
- **D-RES-5:** Vertical definitions stored as declarative YAML in `args/research_config.yaml` — add new verticals without code changes (D26 pattern)
- **D-RES-6:** All research tables prefixed `research_` — namespace isolation from Innovation/Creative engines (D-CPMP-1 pattern)
- **D-RES-7:** Academic paper scanning uses public APIs (Semantic Scholar, arXiv) — no paywalled sources, air-gap degradation skips this stream gracefully
- **D-RES-8:** Patent scanning uses USPTO Open Data — free, no authentication required; air-gap degradation skips gracefully
- **D-RES-9:** Dossier generation is template-based (not LLM-generated) — deterministic, reproducible, auditable (D208 pattern)
- **D-RES-10:** HITL review mandatory before child app fitness assessment — dossier must be in `reviewed` state before triggering `/icdev-agentic` pipeline
- **D-RES-11:** High-scoring challenges cross-register to `innovation_signals` and `creative_signals` tables — enables trend detection across all three engines (D360 pattern)
- **D-RES-12:** Build-vs-buy analysis uses deterministic weighted matrix — compares open-source maturity, commercial cost, ICDEV™ capability gap, compliance risk, integration effort
- **D-RES-13:** Regulatory scan results cached with TTL — regulatory bodies change slowly; 7-day cache prevents redundant requests (rate-limit safe)

## Pipeline Overview

```
SCOPE (define vertical + constraints)
    -> LANDSCAPE (competitive + commercial mapping)
        -> REGULATE (regulatory body + compliance requirements)
            -> COMMUNITY (forums + review sites + pain points)
                -> ACADEMIC (papers + patents + emerging tech)
                    -> BUILD_BUY (open-source + SaaS + gap analysis)
                        -> SYNTHESIZE (challenge scoring + ranking)
                            -> DOSSIER (template-based report generation)
```

## Session Lifecycle

```
created -> scoping -> scanning -> synthesizing -> dossier_ready -> reviewed -> child_app_triggered -> archived
```

| State | Description |
|-------|-------------|
| `created` | Session initialized with vertical and constraints |
| `scoping` | Vertical definition loaded, data stream config resolved |
| `scanning` | Active scanning across 8 data streams (stages 2-6) |
| `synthesizing` | Challenge scoring, ranking, build-vs-buy analysis (stage 7) |
| `dossier_ready` | Dossier generated, awaiting human review |
| `reviewed` | Human reviewed and approved (or rejected with notes) |
| `child_app_triggered` | Dossier passed to agentic fitness assessment pipeline |
| `archived` | Research cycle complete, dossier stored for reference |

State transitions are append-only in `research_session_log` (D6 pattern). Rejected sessions return to `dossier_ready` with reviewer notes.

## Tools

| Tool | File | Purpose |
|------|------|---------|
| Research Manager | `tools/research/research_manager.py` | Main orchestrator + session lifecycle management |
| Vertical Loader | `tools/research/vertical_loader.py` | Load and validate vertical definitions from config |
| Landscape Scanner | `tools/research/landscape_scanner.py` | Competitive + commercial product mapping |
| Regulatory Scanner | `tools/research/regulatory_scanner.py` | Regulatory body + compliance requirement scanning |
| Community Scanner | `tools/research/community_scanner.py` | Forum + review site + pain point extraction |
| Academic Scanner | `tools/research/academic_scanner.py` | Academic paper + patent scanning (Semantic Scholar, arXiv, USPTO) |
| Build Buy Analyzer | `tools/research/build_buy_analyzer.py` | Open-source + SaaS gap analysis, build-vs-buy matrix |
| Challenge Scorer | `tools/research/challenge_scorer.py` | 6-dimension challenge scoring + ranking |
| Dossier Generator | `tools/research/dossier_generator.py` | Template-based dossier generation |
| Cross Engine Bridge | `tools/research/cross_engine_bridge.py` | Register findings to Innovation + Creative engines |

## Configuration

- `args/research_config.yaml` — All settings: verticals, data streams, scoring weights, thresholds, caching, scheduling

## Database Tables

| Table | Purpose |
|-------|---------|
| `research_sessions` | Session metadata: vertical, constraints, state, timestamps |
| `research_session_log` | Append-only state transition audit trail |
| `research_landscape` | Competitive + commercial product findings per session |
| `research_regulatory` | Regulatory requirements + compliance mappings per session |
| `research_community` | Forum/review site pain points per session |
| `research_academic` | Academic paper + patent references per session |
| `research_build_buy` | Build-vs-buy analysis results per session |
| `research_challenges` | Scored + ranked challenges per session |
| `research_dossiers` | Generated dossier content + review status |
| `research_cross_registrations` | Cross-engine signal registrations (append-only) |

## Available Verticals

Defined in `args/research_config.yaml`. Initial set:

| Vertical | Key Regulations | Key Pain Points |
|----------|----------------|-----------------|
| `trading` | SEC Rule 17a-4, FINRA 4511, MiFID II, Dodd-Frank | Latency, audit trails, market surveillance, algorithmic trading compliance |
| `healthcare` | HIPAA, HITECH, 21st Century Cures, FDA 21 CFR Part 11 | Interoperability (HL7/FHIR), patient data privacy, clinical decision support |
| `defense` | ITAR, EAR, DFARS 252.204-7012, NIST 800-171, CMMC | CUI handling, cross-domain solutions, secure supply chain, FedRAMP+IL5 |
| `fintech` | PCI DSS, SOX, BSA/AML, GLBA, OCC Guidance | Real-time fraud detection, regulatory reporting, open banking APIs |
| `cybersecurity` | NIST CSF, CISA BODs, FedRAMP, FISMA | Threat intelligence sharing, SOAR automation, zero trust implementation |
| `logistics` | CTPAT, AEO, IMO FAL, FDA FSMA, ITAR (defense logistics) | Supply chain visibility, customs compliance, cold chain monitoring, fleet telematics |

Custom verticals can be added to config without code changes (D-RES-5).

## Stage 1: SCOPE

Define the research engagement:
- Select target vertical from config (or define custom)
- Set geographic scope (US, EU, global)
- Set impact level constraints (IL2-IL6)
- Set compliance framework requirements
- Set budget/timeline constraints for build-vs-buy analysis

Output: Scoped session with resolved vertical definition and data stream configuration.

## Stage 2: LANDSCAPE

Scan competitive and commercial landscape:
1. **Direct competitors** — Products in same vertical serving same buyer persona
2. **Adjacent competitors** — Products that partially overlap (e.g., GRC tools in cybersecurity vertical)
3. **Commercial SaaS** — Established vendors, pricing models, feature matrices
4. **Market size** — TAM/SAM/SOM estimates from public sources (analyst reports, press releases)

Sources: GitHub repos, Product Hunt, G2/Capterra (public pages), Crunchbase (public), company blogs.

## Stage 3: REGULATE

Scan regulatory requirements specific to vertical:
1. **Primary regulations** — Federal/state laws, industry standards (from vertical config)
2. **Compliance frameworks** — Map to ICDEV™-supported frameworks via crosswalk engine
3. **Enforcement actions** — Recent penalties, consent decrees, audit findings (public record)
4. **Upcoming changes** — Proposed rules, comment periods, effective dates
5. **Gap analysis** — Which regulations ICDEV™ already covers vs. gaps requiring new framework catalogs

Sources: Federal Register, regulatory body websites, compliance news sites.

## Stage 4: COMMUNITY

Scan community forums and review sites for pain points:
1. **Forums** — Reddit (vertical-specific subreddits), Stack Overflow/Exchange, industry-specific forums
2. **Review sites** — G2, Capterra, TrustRadius (public reviews)
3. **GitHub Issues** — Open issues on popular vertical-specific repos (bug reports, feature requests)
4. **Support forums** — Vendor community forums (public)

Pain extraction uses deterministic keyword/regex matching (D354 pattern from Creative Engine). Categories: compliance_burden, integration_difficulty, cost_concern, feature_gap, security_worry, performance_issue, usability_problem.

## Stage 5: ACADEMIC

Scan academic and patent literature:
1. **Academic papers** — Semantic Scholar API, arXiv (cs.CR, cs.SE, cs.AI categories relevant to vertical)
2. **Patents** — USPTO Open Data API (recent filings in vertical domain)
3. **Standards bodies** — IEEE, ISO, NIST publications relevant to vertical
4. **Emerging tech** — Novel approaches, architectures, algorithms gaining traction

Output: Annotated bibliography with relevance scores and ICDEV™ applicability notes.

## Stage 6: BUILD_BUY

Analyze build-vs-buy for top challenges:
1. **Open-source options** — GitHub repos with matching functionality, license check (D202 — block GPL/AGPL/SSPL)
2. **Commercial options** — SaaS products, pricing, integration effort
3. **ICDEV™ capability gap** — What ICDEV™ can already do vs. what needs building
4. **Build cost estimate** — T-shirt sizing (S/M/L/XL) based on complexity
5. **Compliance risk** — Build maintains compliance control; buy may introduce supply chain risk

Build-vs-buy matrix (deterministic weighted average):

| Dimension | Weight | Build Favored When | Buy Favored When |
|-----------|--------|--------------------|------------------|
| Compliance Control | 0.30 | High compliance requirements | Low compliance sensitivity |
| Integration Effort | 0.25 | Deep ICDEV™ integration needed | Standalone usage acceptable |
| Cost (3-year TCO) | 0.20 | Open-source alternative exists | Custom build cost exceeds SaaS |
| Time to Market | 0.15 | No deadline pressure | Urgent market need |
| Competitive Advantage | 0.10 | Core differentiator | Commodity feature |

## Stage 7: SYNTHESIZE

Score and rank industry challenges:

### 6-Dimension Challenge Scoring

| Dimension | Weight | Metric |
|-----------|--------|--------|
| Market Demand | 0.25 | Community pain frequency, review complaints, forum mentions |
| Regulatory Pressure | 0.20 | Enforcement actions, upcoming deadlines, penalty severity |
| Technical Complexity | 0.15 | Inversely scored — simpler challenges rank higher for feasibility |
| Competitive Saturation | 0.15 | Inversely scored — less competition = more opportunity |
| ICDEV™ Readiness | 0.15 | Existing tool/framework coverage, build effort estimate |
| Compliance Alignment | 0.10 | Strengthens ICDEV™ compliance posture, crosswalk coverage |

### Thresholds
- **>= 0.75** — High-priority challenge, recommend for child app feature set
- **0.50 - 0.74** — Medium-priority, include in dossier as secondary opportunity
- **< 0.50** — Low-priority, log for trend tracking only

## Stage 8: DOSSIER

Generate structured research dossier (template-based, D-RES-9):

### Dossier Sections
1. **Executive Summary** — Vertical overview, top 5 challenges, recommended approach
2. **Market Landscape** — Competitors, market size, buyer personas
3. **Regulatory Environment** — Primary regulations, ICDEV™ coverage, gaps
4. **Community Pain Points** — Top pain points by frequency, severity, category
5. **Academic & Patent Landscape** — Emerging tech, relevant research, patent activity
6. **Build-vs-Buy Analysis** — Per-challenge recommendation with justification
7. **Challenge Ranking** — Full scored list with 6-dimension breakdown
8. **ICDEV™ Capability Map** — What ICDEV™ already covers, what needs building
9. **Recommended Child App Scope** — Features, compliance frameworks, agents needed
10. **Risk Assessment** — Technical, regulatory, market, compliance risks
11. **Appendix** — Source citations, raw data references, methodology notes

## Cross-Engine Registration (D-RES-11)

High-scoring challenges (>= 0.75) are cross-registered:
- **Innovation Engine** — As `innovation_signals` with `source_type='industry_research'`, enabling trend detection across research + innovation + creative pipelines
- **Creative Engine** — As `creative_pain_points` with `source='research_engine'`, enabling customer pain point clustering

Registration is append-only and audited in `research_cross_registrations`.

## HITL Review (D-RES-10)

Before triggering child app fitness assessment:
1. Dossier must be in `dossier_ready` state
2. Human reviews dossier content, challenge rankings, build-vs-buy recommendations
3. Reviewer approves (state -> `reviewed`) or rejects with notes (stays `dossier_ready`)
4. Only `reviewed` dossiers can trigger `/icdev-agentic` fitness assessment
5. Review decision recorded in `research_session_log` (append-only, NIST AU-2)

## Security Gates

| Gate | Condition |
|------|-----------|
| License Check | No GPL/AGPL/SSPL in build-vs-buy recommendations (D202) |
| HITL Review | Dossier must be human-reviewed before child app trigger (D-RES-10) |
| Compliance Alignment | Dossier recommendations must not weaken existing compliance posture |
| Source Verification | All data stream sources must be public, no paywalled/classified sources |
| Cross-Registration Audit | All cross-engine registrations logged (append-only) |
| Budget Cap | Max 5 active research sessions per tenant (configurable) |

## Error Handling

- **Web scanner failures:** Log error, continue with other data streams. Dossier notes which streams were unavailable.
- **Database missing:** Return error with migration instructions (`python tools/db/init_icdev_db.py`).
- **Air-gapped mode:** Skip web-dependent streams (landscape, community, academic, regulatory web sources). Run with ICDEV™ internal data only. Dossier notes reduced coverage.
- **Rate limiting:** Per-source configurable backoff. Semantic Scholar: 100/5min, arXiv: 3/sec, USPTO: 60/min.
- **Session resume:** Scanning stage is resumable — completed streams are cached, failed streams retried.
- **Empty results:** If a data stream returns zero results, log warning and continue. Dossier notes the gap.
- **Vertical not found:** Return error listing available verticals from config.

## Edge Cases

1. **Same challenge found in multiple streams** — Dedup by content_hash, merge source citations, boost score
2. **Regulation changes mid-research** — Regulatory cache TTL is 7 days (D-RES-13); manual cache invalidation available
3. **Vertical has no academic literature** — Academic stage returns empty, dossier notes "No academic coverage found"
4. **All build-vs-buy results favor "buy"** — Valid outcome; dossier recommends integration strategy instead of child app
5. **Dossier rejected multiple times** — Session stays in `dossier_ready`, rejection history in session log, no auto-escalation
6. **Cross-engine registration fails** — Non-blocking; log error, dossier still valid. Retry on next pipeline run
7. **Vertical overlaps multiple ICDEV™ compliance frameworks** — Feature, not bug; crosswalk engine handles multi-framework mapping
8. **Research session abandoned** — Sessions older than 30 days in non-terminal state auto-archive with `reason='timeout'`
