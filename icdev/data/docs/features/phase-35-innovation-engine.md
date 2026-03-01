# Phase 35 — Innovation Engine

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 35 |
| Title | Innovation Engine -- Autonomous Self-Improvement |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 22 (Marketplace), Phase 23 (Universal Compliance), Phase 29 (Proactive Monitoring) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV builds and maintains Gov/DoD applications in a rapidly evolving threat landscape. New CVEs are published daily, compliance frameworks are updated quarterly, competitor tools release new capabilities monthly, and developer communities continuously discover better patterns and practices. Currently, ICDEV discovers these changes only when a human operator manually investigates -- after a gate fails, a vulnerability is exploited, or a customer reports a gap. This reactive posture means ICDEV is always behind the curve, patching yesterday's problems instead of anticipating tomorrow's.

The cost of this lag is compounded in the Gov/DoD context. A compliance framework update that goes unnoticed for 90 days can invalidate an ATO. A critical CVE that is not triaged within SLA can trigger a CSSP incident. A competitor capability gap that is not addressed can lose a contract re-compete. An internal optimization that works in one child application but is never propagated wastes engineering effort across the fleet.

The Innovation Engine transforms ICDEV from a reactive tool that waits for instructions into a proactive system that continuously discovers, evaluates, and integrates improvements. It scans external sources (GitHub trending, NVD, Stack Overflow, Hacker News, package registries, compliance feeds), mines internal telemetry (failed self-heals, gate failure frequency, unused tools, slow pipeline stages, NLQ gaps), monitors competitors and standards bodies, and feeds all discoveries through a 5-stage compliance triage pipeline before generating solution specifications. The entire pipeline operates within strict guardrails: budget caps (max 10 auto-solutions per PI), license scanning (no GPL/AGPL/SSPL), boundary impact assessment, and mandatory compliance preservation.

---

## 2. Goals

1. Implement web intelligence scanning across 6 external source categories (GitHub, NVD, Stack Overflow, Hacker News, package registries, compliance feeds) with per-source configurable scan frequency
2. Enable introspective analysis that mines internal ICDEV telemetry for improvement opportunities: failed self-heals, gate failure frequency, unused tools, slow pipeline stages, NLQ gaps, and knowledge gaps -- fully air-gap safe (D203)
3. Score all discovered signals across 5 weighted dimensions: community demand (0.30), impact breadth (0.25), feasibility (0.20), compliance alignment (0.15), and novelty (0.10)
4. Triage signals through a 5-stage compliance gate: classify signal, GOTCHA fit check, boundary impact assessment, compliance pre-check, and duplicate/license check
5. Generate template-based solution specifications (not LLM-generated) with problem statement, GOTCHA layer mapping, acceptance criteria, compliance impact, and test plan (D208)
6. Support competitive intelligence by monitoring GitHub-based competitors (backstage, snyk, trivy, checkov) for feature releases and performing gap analysis (D205)
7. Monitor standards body publications (NIST CSRC, CISA advisories, DoD CIO memos, FedRAMP updates) with graceful degradation when offline (D204)
8. Implement feedback calibration that adjusts scoring weights based on marketplace adoption metrics, with a maximum 0.02 step per calibration cycle and minimum 10 data points

---

## 3. Architecture

```
+---------------------------------------------------------------+
|                    Innovation Engine Pipeline                   |
|                                                                |
|  STAGE 1: DISCOVER                                             |
|  +-------------------+  +-------------------+                  |
|  | Web Scanner       |  | Introspective     |                  |
|  | GitHub, NVD, SO,  |  | Analyzer          |                  |
|  | HN, PyPI, npm,    |  | Failed heals,     |                  |
|  | FedRAMP, CMMC     |  | gate failures,    |                  |
|  |                   |  | slow stages,      |                  |
|  +--------+----------+  | unused tools      |                  |
|           |              +--------+----------+                  |
|           |                       |                             |
|  +--------+-----------+  +-------+----------+                  |
|  | Competitive Intel  |  | Standards Monitor |                  |
|  | GitHub repos       |  | NIST, CISA, DoD   |                  |
|  | Feature tracking   |  | FedRAMP updates    |                  |
|  +--------+-----------+  +-------+----------+                  |
|           |                       |                             |
|           +-----------+-----------+                             |
|                       |                                        |
|  STAGE 2: SCORE       v                                        |
|  +---------------------------------------------------+        |
|  | Signal Ranker — 5-Dimension Weighted Average       |        |
|  | Community(0.30) + Impact(0.25) + Feasibility(0.20) |        |
|  | + Compliance(0.15) + Novelty(0.10)                 |        |
|  | >= 0.80: auto-queue | 0.50-0.79: suggest | <0.50: log     |
|  +---------------------------------------------------+        |
|                       |                                        |
|  STAGE 3: TRIAGE      v                                        |
|  +---------------------------------------------------+        |
|  | 5-Stage Compliance Gate                            |        |
|  | 1. Classify -> 2. GOTCHA Fit -> 3. Boundary       |        |
|  | 4. Compliance Pre-Check -> 5. Dedup/License        |        |
|  +---------------------------------------------------+        |
|                       |                                        |
|  STAGE 4: GENERATE    v                                        |
|  +---------------------------------------------------+        |
|  | Solution Generator (template-based, not LLM)      |        |
|  | Problem + GOTCHA Map + Acceptance + Tests          |        |
|  +---------------------------------------------------+        |
|                       |                                        |
|  STAGES 5-7           v                                        |
|  +---------------------------------------------------+        |
|  | BUILD (ATLAS/M-ATLAS) -> PUBLISH (marketplace     |        |
|  | 7-gate) -> MEASURE + CALIBRATE (feedback loop)    |        |
|  +---------------------------------------------------+        |
+---------------------------------------------------------------+
```

### Discovery Sources

| Source | Category | Scan Frequency | Air-Gap Safe |
|--------|----------|---------------|--------------|
| GitHub Trending | Developer patterns | 6 hours | No |
| NVD (NIST) | CVE/vulnerability | 2 hours | No |
| Stack Overflow | Developer pain points | 12 hours | No |
| Hacker News | Industry trends | 12 hours | No |
| Package Registries | New tools/libraries | 24 hours | No |
| Compliance Feeds | Framework updates | 24 hours | No |
| Internal Telemetry | Self-improvement | 1 hour | Yes |
| Competitor Repos | Gap analysis | 24 hours | No |
| Standards Bodies | NIST/CISA/DoD | 24 hours | No |

---

## 4. Requirements

### 4.1 Discovery

#### REQ-35-001: Web Intelligence Scanning
The system SHALL scan 6 external source categories (GitHub, NVD, Stack Overflow, Hacker News, package registries, compliance feeds) for innovation signals, with per-source configurable scan frequency defined in `args/innovation_config.yaml`.

#### REQ-35-002: Introspective Analysis (D203)
The system SHALL perform introspective analysis that mines internal ICDEV telemetry (failed self-heals, gate failures, unused tools, slow pipelines, NLQ gaps, knowledge gaps) for improvement opportunities. This analysis SHALL be fully air-gap safe with no external network dependency.

#### REQ-35-003: Competitive Intelligence (D205)
The system SHALL monitor GitHub-based competitor repositories (backstage, snyk, trivy, checkov) for feature releases and generate gap analysis reports comparing competitor capabilities to ICDEV features.

#### REQ-35-004: Standards Monitoring (D204)
The system SHALL monitor standards body publications (NIST CSRC, CISA, DoD CIO, FedRAMP) and degrade gracefully when offline, skipping HTTP requests and logging warnings without failing the pipeline.

### 4.2 Scoring

#### REQ-35-005: Five-Dimension Scoring
The system SHALL score every discovered signal across 5 weighted dimensions: community demand (0.30), impact breadth (0.25), feasibility (0.20), compliance alignment (0.15), and novelty (0.10).

#### REQ-35-006: Score Thresholds (D200)
The system SHALL apply human-in-the-loop thresholds: score >= 0.80 auto-queues for solution generation, 0.50-0.79 suggests to human for approval, < 0.50 logs for trend analysis only.

### 4.3 Triage

#### REQ-35-007: Five-Stage Compliance Triage
Every signal SHALL pass through all 5 triage stages: (1) classify signal by category, (2) GOTCHA fit check (must map to Goal/Tool/Arg/Context/HardPrompt), (3) boundary impact assessment (GREEN/YELLOW/ORANGE/RED), (4) compliance pre-check (must not weaken compliance posture), and (5) duplicate and license check.

#### REQ-35-008: License Blocking (D202)
The triage engine SHALL block signals associated with GPL, AGPL, or SSPL licensed components (copyleft risk for Gov/DoD environments).

#### REQ-35-009: Duplicate Detection
The triage engine SHALL detect duplicate signals using content hash comparison with a similarity threshold of 0.85, preventing redundant solution generation.

### 4.4 Generation and Feedback

#### REQ-35-010: Template-Based Solution Specs (D208)
The system SHALL generate solution specifications from templates (not LLM-generated) containing: problem statement, GOTCHA layer mapping, proposed solution, acceptance criteria (BDD-style), compliance impact assessment, test plan, marketplace asset type, and estimated effort.

#### REQ-35-011: Budget Cap (D201)
The system SHALL enforce a maximum of 10 auto-generated solutions per Program Increment to prevent scope creep.

#### REQ-35-012: Feedback Calibration
The system SHALL calibrate scoring weights based on marketplace adoption metrics (install count, rating, self-heal hits, gate failure reduction), with a maximum adjustment of 0.02 per calibration cycle and a minimum of 10 data points required.

#### REQ-35-013: Daemon Mode
The system SHALL support continuous background scanning in daemon mode with configurable quiet hours (default 02:00-06:00 UTC) during which no solution generation occurs.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `innovation_signals` | Discovered signals (append-only): source, category, title, content_hash, score, triaged, created_at |
| `innovation_triage_log` | Triage decisions per signal (append-only): signal_id, stage, result, reason, triaged_at |
| `innovation_solutions` | Generated solution specifications: signal_id, spec_json, status, marketplace_asset_id |
| `innovation_trends` | Detected trend clusters: keywords, signal_ids, trend_score, detected_at |
| `innovation_competitor_scans` | Competitive intel scan results: competitor, features_json, gaps_json, scanned_at |
| `innovation_standards_updates` | Standards body change tracking: source, publication_id, title, impact, detected_at |
| `innovation_feedback` | Feedback loop metrics for calibration: solution_id, installs, rating, self_heal_hits, gate_reduction |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/innovation/innovation_manager.py` | Main orchestrator: full pipeline, status, pipeline report, daemon mode |
| `tools/innovation/web_scanner.py` | Scan GitHub, NVD, Stack Overflow, HN, package registries, compliance feeds |
| `tools/innovation/signal_ranker.py` | 5-dimension innovation scoring with feedback calibration |
| `tools/innovation/triage_engine.py` | 5-stage compliance-first triage pipeline |
| `tools/innovation/trend_detector.py` | Cross-signal pattern detection via keyword co-occurrence (D207) |
| `tools/innovation/solution_generator.py` | Template-based solution spec generation (D208) |
| `tools/innovation/introspective_analyzer.py` | Internal telemetry mining (air-gap safe) |
| `tools/innovation/competitive_intel.py` | Competitor feature monitoring and gap analysis |
| `tools/innovation/standards_monitor.py` | NIST/CISA/DoD/FedRAMP change tracking |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D199 | Scan frequency configurable per source in `args/innovation_config.yaml` | D26 declarative pattern; different sources have different update frequencies and rate limits |
| D200 | Human-in-the-loop: score >= 0.80 auto-queues, 0.50-0.79 suggests, < 0.50 logs only | Balances automation efficiency with human judgment for medium-confidence signals |
| D201 | Innovation budget: max 10 auto-generated solutions per PI | Prevents scope creep; forces prioritization of highest-value innovations |
| D202 | IP/license scanning blocks GPL/AGPL/SSPL (copyleft risk for Gov/DoD) | Gov/DoD contracts typically prohibit copyleft; blocking at triage prevents downstream contamination |
| D203 | Introspective analysis is air-gap safe (reads internal DB only) | Air-gapped IL6/SIPR deployments still benefit from internal self-improvement |
| D204 | Standards body monitoring degrades gracefully when offline | Skips HTTP requests, logs warning, continues pipeline; no hard failure on network unavailability |
| D205 | Competitive intel for GitHub-based competitors only | Website scraping requires additional legal/technical setup; GitHub API is structured and reliable |
| D206 | All innovation signals are append-only in DB (D6 pattern) | Triage decisions audited; no signal can be silently deleted or modified |
| D207 | Trend detection uses deterministic keyword co-occurrence (no LLM) | Air-gap safe, reproducible, zero external dependency; LLM not needed for pattern matching |
| D208 | Solution specs are template-based (not LLM-generated) | Deterministic, auditable, consistent format; validated by spec_quality_checker |

---

## 8. Security Gate

**Innovation Security Gate:**
- License check: no GPL/AGPL/SSPL components (copyleft risk for Gov/DoD)
- Boundary impact: RED items blocked from auto-generation, require manual review
- Compliance alignment: signals that would weaken existing compliance posture are blocked
- GOTCHA fit: signals must map to at least one GOTCHA layer (Goal/Tool/Arg/Context/HardPrompt)
- Duplicate detection: content hash dedup with similarity > 0.85 prevents redundant work
- Budget cap: max 10 auto-solutions per PI enforced at the solution generation stage
- Build gates: all generated solutions must pass existing security gates (SAST, deps, secrets, CUI)
- Marketplace publish: solutions destined for marketplace must pass the 7-gate pipeline

---

## 9. Commands

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

**CUI // SP-CTI**
