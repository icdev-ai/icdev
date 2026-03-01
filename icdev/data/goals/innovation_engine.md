# Innovation Engine — Autonomous Self-Improvement Pipeline

CUI // SP-CTI

## Purpose

The Innovation Engine enables ICDEV to continuously and autonomously improve itself by:
1. Discovering developer pain points, CVEs, compliance changes, and competitive gaps
2. Scoring and triaging discoveries through compliance-first gates
3. Generating solution specifications using existing ATLAS/M-ATLAS workflow
4. Publishing solutions to the ICDEV marketplace for ecosystem-wide benefit
5. Learning from feedback to improve future discovery quality

## Architecture Decision Records

- **D199:** Scan frequency configurable per source in `args/innovation_config.yaml` (D26 pattern)
- **D200:** Human-in-the-loop threshold: score >= 0.80 auto-queues, 0.50-0.79 suggests, < 0.50 logs only
- **D201:** Innovation budget: max 10 auto-generated solutions per PI to prevent scope creep
- **D202:** IP/license scanning blocks GPL/AGPL/SSPL solutions (copyleft risk for Gov/DoD)
- **D203:** Introspective analysis is air-gap safe (no web access needed, reads internal DB only)
- **D204:** Standards body monitoring degrades gracefully when offline (skips HTTP, logs warning)
- **D205:** Competitive intel for GitHub-based competitors only (website scraping requires additional setup)
- **D206:** All innovation signals are append-only in DB (D6 pattern), triage decisions are audited
- **D207:** Trend detection uses deterministic keyword co-occurrence (no LLM required, air-gap safe)
- **D208:** Solution specs are template-based (not LLM-generated), validated by spec_quality_checker

## Pipeline Overview

```
DISCOVER (web + introspective + competitive + standards)
    → SCORE (5-dimension weighted average)
        → TRIAGE (5-stage compliance gate)
            → GENERATE (template-based spec)
                → BUILD (ATLAS/M-ATLAS TDD)
                    → PUBLISH (marketplace 7-gate pipeline)
                        → MEASURE (adoption + impact)
                            → CALIBRATE (weight adjustment)
```

## Tools

| Tool | File | Purpose |
|------|------|---------|
| Web Scanner | `tools/innovation/web_scanner.py` | Scan GitHub, NVD, Stack Overflow, HN for signals |
| Signal Ranker | `tools/innovation/signal_ranker.py` | 5-dimension innovation scoring |
| Trend Detector | `tools/innovation/trend_detector.py` | Cross-signal pattern detection |
| Triage Engine | `tools/innovation/triage_engine.py` | 5-stage compliance-first triage |
| Solution Generator | `tools/innovation/solution_generator.py` | Auto-generate solution specs |
| Innovation Manager | `tools/innovation/innovation_manager.py` | Main orchestrator + daemon mode |
| Introspective Analyzer | `tools/innovation/introspective_analyzer.py` | Internal telemetry mining |
| Competitive Intel | `tools/innovation/competitive_intel.py` | Competitor feature monitoring |
| Standards Monitor | `tools/innovation/standards_monitor.py` | NIST/CISA/DoD change tracking |
| MCP Server | `tools/mcp/innovation_server.py` | MCP tools for Claude Code integration |

## Configuration

- `args/innovation_config.yaml` — All settings: sources, scoring weights, triage rules, scheduling

## Database Tables

| Table | Purpose |
|-------|---------|
| `innovation_signals` | Discovered signals (append-only) |
| `innovation_triage_log` | Triage decisions per signal (append-only) |
| `innovation_solutions` | Generated solution specs |
| `innovation_trends` | Detected trend clusters |
| `innovation_competitor_scans` | Competitive intel scan results |
| `innovation_standards_updates` | Standards body change tracking |
| `innovation_feedback` | Feedback loop metrics for calibration |

## Stage 1: Discovery

### Web Intelligence Sources
1. **GitHub** — Trending repos, issues (bug/enhancement/security), discussions
2. **CVE Databases** — NVD (CRITICAL/HIGH), GitHub Security Advisories
3. **Stack Overflow** — Top-voted questions in DevSecOps/compliance/K8s/IaC tags
4. **Hacker News** — High-score stories on security/devops/compliance topics
5. **Package Registries** — PyPI/npm trending packages in security/compliance categories
6. **Compliance Updates** — FedRAMP marketplace, CMMC AB, Federal Register

### Introspective Sources (Air-Gap Safe)
1. **Failed Self-Heals** — ICDEV problems it can't solve yet (confidence < 0.3)
2. **Gate Failure Frequency** — Which gates fail most? Build better tooling
3. **Unused Tools** — Improve discoverability or deprecate
4. **Slow Pipeline Stages** — Performance optimization targets
5. **NLQ Gaps** — Questions with no answers = knowledge gaps
6. **Knowledge Gaps** — Self-heal patterns with no resolution

### Competitive Intelligence
- Monitor GitHub repos: backstage, snyk, trivy, checkov
- Track releases and new features
- Gap analysis: what competitors have that ICDEV doesn't

### Standards Body Monitoring
- NIST CSRC publications (SP 800, FIPS, IR series)
- CISA advisories and binding operational directives
- DoD CIO memos (zero trust, DevSecOps, CMMC, cATO, MOSA)
- FedRAMP updates and marketplace changes

## Stage 2: Scoring

5-dimension weighted average (D21 pattern):

| Dimension | Weight | Metric |
|-----------|--------|--------|
| Community Demand | 0.30 | Stars, votes, upvotes, issue frequency |
| Impact Breadth | 0.25 | ICDEV tenants/projects potentially affected |
| Feasibility | 0.20 | Can ICDEV build with existing tools? |
| Compliance Alignment | 0.15 | Strengthens (not weakens) compliance |
| Novelty | 0.10 | Not already addressed by ICDEV |

### Thresholds
- **>= 0.80** — Auto-queue for solution generation
- **0.50 - 0.79** — Suggest to human, require approval
- **< 0.50** — Log for trend analysis only

## Stage 3: Triage (5-Stage Compliance Gate)

Every signal passes through ALL 5 stages:

1. **Classify Signal** — Map to category via keyword matching
2. **GOTCHA Fit Check** — Must map to Goal/Tool/Arg/Context/HardPrompt
3. **Boundary Impact** — GREEN/YELLOW/ORANGE/RED assessment
4. **Compliance Pre-Check** — Block if would weaken compliance posture
5. **Duplicate/License Check** — Dedup + license compatibility

### Blocking Rules
- RED boundary impact → BLOCKED (no auto-generation)
- Compliance-weakening detected → BLOCKED
- GPL/AGPL/SSPL license → BLOCKED
- Duplicate signal (similarity > 0.85) → BLOCKED
- No GOTCHA layer fit → BLOCKED

## Stage 4: Solution Generation

Template-based spec generation with sections:
1. Problem Statement
2. GOTCHA Layer mapping
3. Proposed Solution (layer-specific template)
4. Acceptance Criteria (BDD-style)
5. Compliance Impact assessment
6. Test Plan (unit + BDD)
7. Marketplace Asset Type
8. Estimated Effort (S/M/L/XL)

## Stage 5-6: Build & Publish

Reuse existing ICDEV pipelines:
- **Build:** ATLAS/M-ATLAS workflow via `/icdev-build`
- **Test:** Full test suite via `/icdev-test`
- **Security:** SAST + dependency audit via `/icdev-secure`
- **Compliance:** CUI markings + STIG via `/icdev-comply`
- **Publish:** Marketplace 7-gate pipeline via `/icdev-market`

## Stage 7: Feedback & Calibration

Metrics that feed back into scoring weight calibration:
1. Marketplace install count (high installs = good signal quality)
2. Marketplace rating (high ratings = good solution quality)
3. Self-heal pattern hits (patterns that prevent future failures)
4. Gate failure reduction (solutions that reduce gate failures)
5. Tenant feature requests (addressed vs total)

Weight adjustment: max 0.02 step per calibration cycle, min 10 data points.

## Scheduling

- **Daemon mode:** Continuous background scanning
- **Quiet hours:** No solution generation during 02:00-06:00 UTC
- **Budget:** Max 10 auto-generated solutions per PI
- **Rate limiting:** Per-source configurable (GitHub: 60/hr, NVD: careful)

## Security Gates

| Gate | Condition |
|------|-----------|
| Innovation Triage | 5-stage compliance check must pass |
| License Check | No GPL/AGPL/SSPL (copyleft risk) |
| Boundary Impact | RED items blocked from auto-generation |
| Compliance Alignment | Must not weaken existing compliance posture |
| Solution Build | Must pass all existing security gates (SAST, deps, secrets, CUI) |
| Marketplace Publish | Must pass 7-gate marketplace pipeline |
| Budget Cap | Max 10 auto-solutions per PI |

## Error Handling

- Web scanner failures: log error signal, continue with other sources
- Database missing: return error with migration instructions
- Air-gapped mode: skip web sources, run introspective analysis only
- Rate limiting: back off and retry with exponential delay
- Budget exceeded: log signal for next PI, don't generate solution

## Edge Cases

1. **Same CVE from NVD + GitHub Advisories** — Dedup by content_hash (CVE ID)
2. **Competitor releases a feature ICDEV already has** — Novelty score = 0, auto-logged
3. **Standards body publishes draft (not final)** — Flag as draft, lower priority
4. **Innovation signal maps to multiple GOTCHA layers** — Pick primary, note others
5. **Solution spec fails quality check** — Block generation, log for manual review
