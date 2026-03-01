# Phase 58 — Creative Engine: Customer-Centric Feature Discovery

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 58 |
| Title | Creative Engine: Customer-Centric Feature Discovery |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 35 (Innovation Engine) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-27 |

---

## 1. Problem Statement

ICDEV's Innovation Engine (Phase 35) discovers technical improvement opportunities from CVE feeds, package registries, standards bodies, and internal telemetry. However, it has no visibility into *customer-facing* pain points — the frustrations users voice on review sites, community forums, and GitHub issue trackers. When a product manager asks "what features are competitors missing?" or "what do users complain about most?", there is no systematic answer. Feature opportunity discovery is manual, ad hoc, and biased toward internal engineering priorities rather than customer needs.

Without customer-centric intelligence, ICDEV cannot:
- Identify unmet user needs that competitors fail to address
- Quantify pain point frequency and severity from real user feedback
- Detect emerging feature trends before they become table stakes
- Generate evidence-backed feature specifications with competitive justification
- Bridge customer voice signals into the Innovation Engine for prioritized development

Phase 58 closes this gap with a 5-stage pipeline that automates competitor discovery, pain point extraction, gap scoring, trend detection, and spec generation — all using deterministic methods that are air-gap safe and reproducible.

---

## 2. Goals

1. Auto-discover competitors from G2, Capterra, TrustRadius category pages with advisory-only confirmation (D353)
2. Scan 7 source types (review sites, forums, GitHub issues, Product Hunt, GovCon blogs) for customer signals
3. Extract pain points via deterministic keyword matching and sentiment analysis — no LLM required (D354)
4. Classify pain points into 15 categories (ux, performance, integration, pricing, compliance, security, reporting, customization, support, scalability, documentation, onboarding, api, automation, other)
5. Score feature gaps using a 3-dimension weighted composite: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25) (D355)
6. Detect trends via keyword co-occurrence with velocity/acceleration lifecycle tracking (emerging, active, declining, stale)
7. Generate template-based feature specs with problem statement, evidence, user quotes, competitive landscape, and acceptance criteria (D356)
8. Cross-register high-scoring signals to the Innovation Engine for unified trend detection (D360)
9. Support daemon mode with configurable quiet hours for continuous monitoring (D359)

---

## 3. Architecture

```
              Creative Engine Pipeline (D351)
    ┌───────────────────────────────────────────────────┐
    │            args/creative_config.yaml               │
    │  (domain, sources, scoring, thresholds, scheduling)│
    └─────────────────────┬─────────────────────────────┘
                          │
    ┌─────────┬───────────┼───────────┬──────────┐
    ↓         ↓           ↓           ↓          ↓
 DISCOVER   EXTRACT     SCORE       RANK      GENERATE
 (competitor (pain_     (gap_       (trend_   (spec_
  discoverer  extractor)  scorer)    tracker)   generator)
  + source_
  scanner)
    │         │           │           │          │
    ↓         ↓           ↓           ↓          ↓
 creative_  creative_   creative_   creative_  creative_
 competitors signals    pain_points feature_   specs
 + creative_            (scored)    gaps       (template-
  signals                           + creative  based)
 (append-only)                       trends       │
    │                                    │         │
    │              ┌─────────────────────┘         │
    │              ↓                                ↓
    │     Innovation Engine                  Dashboard API
    │     (cross-registration               /api/creative/*
    │      via D360 bridge)                 /proposals page
    │              │
    └──────────────┘
         Audit Trail
         (append-only, D6)
```

### Key Design Principles

- **Separate from Innovation Engine** — Different domain (customer voice vs. technical signals), different scoring (3-dimension vs. 5-dimension), different sources (D351)
- **Deterministic extraction** — Keyword/regex-based pain extraction, no LLM dependency, air-gap safe (D354)
- **Advisory-only discovery** — Competitor auto-discovery stores as `discovered`; human must confirm before tracking activates (D353)
- **Template-based generation** — Feature specs use templates, not LLM, ensuring reproducible output (D356)
- **Append-only storage** — All tables except `creative_competitors` are append-only (D357, D6)
- **Reusable helpers** — `_safe_get()`, `_get_db()`, `_now()`, `_audit()` copied from web_scanner pattern (D358)

---

## 4. Implementation

### Component 1: Competitor Discoverer (`tools/creative/competitor_discoverer.py`)

Auto-discovers competitors from G2/Capterra/TrustRadius category pages. Stores discovered competitors with `status='discovered'`. Human must confirm (`--confirm`) before scanning activates. Supports `--list` and `--archive` lifecycle management.

### Component 2: Source Scanner (`tools/creative/source_scanner.py`)

Scans 7 configurable source types for customer signals using the function registry pattern (D352):

| Source | Type | Adapter Function |
|--------|------|------------------|
| G2 | Review site | `scan_g2()` |
| Capterra | Review site | `scan_capterra()` |
| TrustRadius | Review site | `scan_trustradius()` |
| Reddit | Forum | `scan_reddit()` |
| GitHub Issues | Issue tracker | `scan_github_issues()` |
| Product Hunt | Launch site | `scan_producthunt()` |
| GovCon Blogs | Industry blogs | `scan_govcon_blogs()` |

Features:
- Per-source rate limiting from `args/creative_config.yaml`
- Content hash deduplication prevents signal re-insertion
- Circuit breaker pattern (D146) for graceful network failure handling
- Air-gapped mode disables all web sources and logs `scan-skipped`
- Competitor-aware: review-site adapters filter for confirmed competitors

### Component 3: Pain Extractor (`tools/creative/pain_extractor.py`)

Extracts pain points from `creative_signals` using deterministic methods only:

- **Keyword extraction** — Term-frequency with hardcoded English stopword removal (~100 words), no NLTK dependency
- **Sentiment classification** — Positive/negative/neutral/mixed via indicator word counting (D354)
- **Category assignment** — Keyword overlap against 15 config-defined categories
- **Severity estimation** — Indicator phrase matching (critical, major, minor, cosmetic)
- **Clustering** — Union-find on keyword fingerprint overlap (signals with >= 3 shared keywords)
- **Deduplication** — Fingerprint-based; new rows inserted with merged state, latest wins on query

### Component 4: Gap Scorer (`tools/creative/gap_scorer.py`)

Scores pain points using a 3-dimension weighted average (D21 deterministic scoring, D355):

| Dimension | Weight | Formula |
|-----------|--------|---------|
| `pain_frequency` | 0.40 | Normalized signal count across sources |
| `gap_uniqueness` | 0.35 | `1.0 - (competitors_addressing / total_confirmed)` |
| `effort_to_impact` | 0.25 | `(frequency * severity_weight) / complexity` |

**Thresholds:**
- `>= 0.75` — Auto-generate feature spec
- `0.50 - 0.74` — Suggest for review
- `< 0.50` — Log only

Stores composite score + dimension breakdown in `creative_pain_points` table. Identifies feature gaps from high-scoring pain points and stores them in `creative_feature_gaps` table.

### Component 5: Trend Tracker (`tools/creative/trend_tracker.py`)

Detects trends via keyword co-occurrence analysis:

- **Velocity** — Rate of new signals per time window
- **Acceleration** — Change in velocity (increasing/decreasing)
- **Lifecycle transitions** — `emerging` (new cluster) -> `active` (growing) -> `declining` (shrinking) -> `stale` (no new signals)
- Deterministic keyword co-occurrence, no LLM (D207 pattern)

### Component 6: Spec Generator (`tools/creative/spec_generator.py`)

Generates template-based feature specifications (D356) for gaps scoring above the `auto_spec` threshold:

Spec contents:
- Problem statement with evidence count
- User quotes from source signals
- Competitive landscape analysis
- Proposed feature description
- Score breakdown (3 dimensions)
- Target persona
- Competitive advantage narrative
- Effort estimate (T-shirt sizing)
- Acceptance criteria

### Component 7: Creative Engine Orchestrator (`tools/creative/creative_engine.py`)

Single entry point coordinating all 6 sub-tools in sequence:

```
competitor_discoverer -> source_scanner -> pain_extractor ->
gap_scorer -> trend_tracker -> spec_generator
```

Supports:
- One-shot full pipeline (`--run`)
- Individual stage execution (`--discover`, `--scan`, `--extract`, `--score`, `--rank`, `--generate`)
- Status queries (`--status`, `--competitors`, `--trends`, `--specs`)
- Continuous daemon mode (`--daemon`) with quiet hours from config (D359)
- Cross-registration of high-scoring signals to `innovation_signals` table (D360)

---

## 5. Database

### 6 Tables (all prefixed `creative_`)

| Table | Append-Only | Purpose |
|-------|-------------|---------|
| `creative_competitors` | No (UPDATE for status) | Discovered/confirmed/archived competitors |
| `creative_signals` | Yes | Raw signals from all 7 source types |
| `creative_pain_points` | Yes | Extracted, categorized, scored pain points |
| `creative_feature_gaps` | Yes | Identified feature gaps from scored pain points |
| `creative_specs` | Yes | Generated template-based feature specifications |
| `creative_trends` | Yes | Trend clusters with velocity and acceleration |

**Status transitions for `creative_competitors`:** `discovered` -> `confirmed` -> `archived`

All other tables follow the D6 append-only audit trail pattern. Content hash deduplication on `creative_signals` prevents duplicate insertion.

---

## 6. Configuration

**File:** `args/creative_config.yaml`

```yaml
# Key configuration sections:
domain:                    # Product domain name and category URLs
sources:                   # Per-source rate limits, endpoints, enable/disable
  review_sites:            # G2, Capterra, TrustRadius
  community_forums:        # Reddit
  github_issues:           # GitHub issue tracker
  producthunt:             # Product Hunt
competitor_discovery:      # Refresh interval, max competitors, auto_confirm: false
extraction:                # Negative/feature-request keywords, 15 categories, clustering params
scoring:
  weights:                 # pain_frequency: 0.40, gap_uniqueness: 0.35, effort_to_impact: 0.25
  thresholds:              # auto_spec: 0.75, suggest: 0.50
spec_generation:           # Template config
innovation_bridge:         # Cross-registration to Innovation Engine (min_score: 0.60)
trends:                    # Trend detection parameters
scheduling:                # Daemon interval, quiet hours
```

---

## 7. Dashboard

Creative Engine results surface through the existing proposals dashboard and API:

- **Proposals page** (`/proposals`) — Opportunity list with stat grid
- **Opportunity detail** (`/proposals/<id>`) — Competitive landscape, sections, timeline
- **API endpoints** — `/api/creative/*` for programmatic access to competitors, signals, gaps, specs, trends

CLI commands provide direct access to all pipeline stages:

```bash
# Full pipeline
python tools/creative/creative_engine.py --run --json

# Individual stages
python tools/creative/creative_engine.py --discover --domain "proposal management" --json
python tools/creative/creative_engine.py --scan --all --json
python tools/creative/creative_engine.py --extract --json
python tools/creative/creative_engine.py --score --json
python tools/creative/creative_engine.py --rank --top-k 20 --json
python tools/creative/creative_engine.py --generate --json

# Status and queries
python tools/creative/creative_engine.py --status --json
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

---

## 8. Security Gates

The Creative Engine operates under existing security gates with no new gate of its own. Key constraints:

- **Append-only audit trail** (D6) — All signal, pain point, gap, spec, and trend records are immutable once written
- **Advisory-only competitor discovery** (D353) — Auto-discovered competitors require human confirmation before tracking activates
- **Content hash deduplication** — Prevents duplicate signal ingestion (similarity threshold in config)
- **Rate limiting** — Per-source rate limits enforced via config to prevent API abuse
- **Air-gapped mode** — All web sources disabled when `environment.mode: air_gapped`; logs `scan-skipped` for each source
- **Innovation Engine cross-registration** — Only signals scoring >= `innovation_bridge.min_score` (default 0.60) are promoted
- **Quiet hours** — Spec generation and daemon scanning paused during configured quiet hours (D359)
- **Circuit breaker** (D146) — Source adapters use circuit breaker pattern for graceful network failure handling

---

## 9. Verification

```bash
# Run all Creative Engine sub-tools to verify pipeline
python tools/creative/competitor_discoverer.py --list --json
python tools/creative/source_scanner.py --list-sources --json
python tools/creative/pain_extractor.py --list --json
python tools/creative/gap_scorer.py --top --limit 10 --json
python tools/creative/trend_tracker.py --report --json
python tools/creative/spec_generator.py --list --json

# Full pipeline dry run
python tools/creative/creative_engine.py --status --json

# Verify database tables exist
python -c "
import sqlite3
from pathlib import Path
db = sqlite3.connect(str(Path('data/icdev.db')))
tables = [r[0] for r in db.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'creative_%'\").fetchall()]
print(f'Creative tables: {sorted(tables)}')
expected = {'creative_competitors','creative_signals','creative_pain_points','creative_feature_gaps','creative_specs','creative_trends'}
missing = expected - set(tables)
print(f'Missing: {missing}' if missing else 'All 6 tables present')
db.close()
"
```

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D351 | Creative Engine separate from Innovation Engine | Different domain (customer voice vs. technical signals), different scoring (3-dimension vs. 5-dimension), different sources (review sites/forums vs. CVE/package/standards feeds) |
| D352 | Source adapters via function registry dict | Follows `web_scanner.py` `SOURCE_SCANNERS` pattern (D66); add new sources without code changes |
| D353 | Competitor auto-discovery is advisory-only | Stores as `status='discovered'`; human must confirm before tracking activates; prevents false positives |
| D354 | Pain extraction is deterministic keyword/regex | Air-gap safe, reproducible, no LLM dependency; uses stdlib only (D13 pattern) |
| D355 | 3-dimension scoring: pain_frequency(0.40) + gap_uniqueness(0.35) + effort_to_impact(0.25) | Deterministic weighted average (D21 pattern); user-configurable weights in YAML (D26 pattern) |
| D356 | Feature specs are template-based | No LLM, reproducible output; follows `solution_generator.py` pattern |
| D357 | All tables append-only except creative_competitors | D6 audit trail pattern; competitors need UPDATE for `discovered` -> `confirmed` -> `archived` status transitions |
| D358 | Reuses `_safe_get()`, `_get_db()`, `_now()`, `_audit()` helpers | Copy-adapted from `web_scanner.py`; consistent helper patterns across codebase |
| D359 | Daemon mode respects quiet hours from config | Consistent with `innovation_manager.py` daemon pattern; prevents unwanted activity during maintenance windows |
| D360 | High-scoring signals cross-register to `innovation_signals` | Enables Innovation Engine trend detection on creative discoveries; bridges customer voice into technical improvement pipeline |

---

## Files

### New Files (8)
| File | Purpose |
|------|---------|
| `tools/creative/__init__.py` | Package |
| `tools/creative/creative_engine.py` | Pipeline orchestrator (one-shot + daemon) |
| `tools/creative/competitor_discoverer.py` | Auto-discover competitors from category pages |
| `tools/creative/source_scanner.py` | 7-source signal scanner with circuit breaker |
| `tools/creative/pain_extractor.py` | Deterministic keyword extraction + clustering |
| `tools/creative/gap_scorer.py` | 3-dimension composite gap scoring |
| `tools/creative/trend_tracker.py` | Trend detection with velocity/acceleration |
| `tools/creative/spec_generator.py` | Template-based feature spec generation |

### Configuration
| File | Purpose |
|------|---------|
| `args/creative_config.yaml` | Domain, sources, scoring weights, thresholds, scheduling |

### Modified Files
| File | Change |
|------|--------|
| `tools/db/init_icdev_db.py` | +6 CREATE TABLE statements (creative_*) |
| `tools/dashboard/app.py` | +Creative Engine API routes |
| `CLAUDE.md` | +D351-D360, +6 tables, +commands, +config, +Creative Engine section |
| `tools/manifest.md` | +Creative Engine section |
| `goals/manifest.md` | +Creative Engine entry |
